"""Switch a generated NiFi flow from local Docker to REST deployment."""

import json

import pandas as pd
from rdflib import Graph, Literal, URIRef
from rdfine import GraphReader, receive_first

from ..compiler_abc import Compiler
from ..utils import attach_file


class NifiRemoteCompiler(Compiler):
    """Emit a one-shot deployer when ``nifi:deploymentMode "remote"`` is set."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        return graph_reader.ask(
            """
            ?pipeline a tcs:PipelineDefinition ;
                nifi:deploymentMode "remote" .
            ?file a spdx:File ;
                tcs:filename "flow.json" ;
                tcs:filepath "nifi" .
            """
        )

    def compile(self) -> Graph:
        flow = json.loads(
            str(
                receive_first(
                    self.output_reader.select(
                        "?content",
                        """
                        ?file a spdx:File ;
                            tcs:filename "flow.json" ;
                            tcs:filepath "nifi" ;
                            tcs:literal ?content .
                        """,
                    )["content"]
                )
            )
        )
        flow["flowContents"] = flow.pop("rootGroup")
        # Persisted local flows use arrays; the upload API's flow-definition DTO
        # uses maps for these currently-empty collections.
        flow["parameterContexts"] = {}
        flow["parameterProviders"] = {}
        flow.pop("maxTimerDrivenThreadCount", None)
        for processor in flow["flowContents"].get("processors", []):
            if processor.get("scheduledState") == "RUNNING":
                processor["scheduledState"] = "ENABLED"
        self.remove_sensitive_properties(flow["flowContents"])
        environment, secrets = self.fetch_deployment_config()

        self.output_reader = attach_file(
            self.output_reader,
            filename="flow_definition.json",
            filepath="nifi",
            content=json.dumps(flow, indent=4),
        )
        self.output_reader = attach_file(
            self.output_reader,
            filename="deploy_flow.py",
            filepath="nifi",
            content=_DEPLOY_SCRIPT.lstrip(),
        )
        self.output_reader = attach_file(
            self.output_reader,
            filename=".env.example",
            filepath=".",
            content=(
                "# Copy this file to .env and fill in the values.\n"
                "# Docker Compose loads .env automatically from this directory.\n"
                "# Never commit .env.\n"
                + "".join(f"{name}=\n" for name in sorted(set(secrets.values())))
            ),
        )
        self.replace_local_compose(flow["flowContents"]["name"], environment, secrets)
        return self.output_reader.graph

    def fetch_deployment_config(self) -> tuple[dict[str, str], dict[str, str]]:
        rows = self.output_reader.select(
            "?username_secret ?password_secret ?gateway_url ?nifi_url ?parent_id",
            """
            ?pipeline a tcs:PipelineDefinition ;
                nifi:deploymentMode "remote" ;
                nifi:deploymentConfig/tcs:embedded ?config .
            ?config nifi:dshUsername/tcs:secretName ?username_secret ;
                nifi:dshPassword/tcs:secretName ?password_secret ;
                nifi:dshGatewayUrl ?gateway_url ;
                nifi:baseUrl ?nifi_url .
            OPTIONAL { ?config nifi:parentProcessGroupId ?parent_id . }
            """,
        )
        if len(rows) != 1:
            raise ValueError(
                'Remote NiFi deployment requires exactly one nifi:deploymentConfig'
            )
        row = rows.iloc[0]
        environment = {
            "DSH_GATEWAY_URL": str(row.gateway_url),
            "NIFI_BASE_URL": str(row.nifi_url),
        }
        secrets = {
            "DSH_USERNAME": str(row.username_secret),
            "DSH_PASSWORD": str(row.password_secret),
        }
        if not pd.isna(row.parent_id) and str(row.parent_id):
            environment["NIFI_PARENT_PG_ID"] = str(row.parent_id)

        azure = self.output_reader.select(
            "?account_name_secret ?sas_token_secret",
            """
            ?step prov:specializationOf
                    nifi:AzureStorageCredentialsControllerService_v12 ;
                p-plan:hasInputVar/tcs:embedded ?properties .
            OPTIONAL {
                ?properties nifi:storageAccountName/tcs:secretName
                    ?account_name_secret .
            }
            OPTIONAL {
                ?properties nifi:sasToken/tcs:secretName ?sas_token_secret .
            }
            """,
        )
        if not azure.empty:
            values = azure.iloc[0]
            if not pd.isna(values.account_name_secret):
                secrets["AZURE_STORAGE_ACCOUNT_NAME"] = str(
                    values.account_name_secret
                )
            if not pd.isna(values.sas_token_secret):
                secrets["AZURE_SAS_TOKEN"] = str(values.sas_token_secret)
        return environment, secrets

    @staticmethod
    def remove_sensitive_properties(flow_contents: dict) -> None:
        """Keep catalog-marked secrets out of the deployable JSON artifact."""
        components = flow_contents.get("processors", []) + flow_contents.get(
            "controllerServices", []
        )
        for component in components:
            descriptors = component.get("propertyDescriptors", {})
            properties = component.get("properties", {})
            for name, descriptor in descriptors.items():
                if descriptor.get("sensitive"):
                    properties.pop(name, None)

    def replace_local_compose(
        self,
        group_name: str,
        environment: dict[str, str],
        secrets: dict[str, str],
    ) -> None:
        config_id = receive_first(
            self.output_reader.select(
                "?config",
                """
                nifi:Orchestrator tcs:config ?config .
                ?config a tcs:DockerComposeConfig .
                """,
            )["config"]
        )
        old_body = self.output_reader.filter(
            sub=config_id, pred=["tcs:literal", "tcs:embedded"]
        ).graph
        self.output_reader = self.output_reader.remove(old_body)

        compose = {
            "services": {
                "nifi-deploy": {
                    "image": "python:3.12-slim",
                    "working_dir": "/deployment",
                    "volumes": ["./nifi:/deployment:ro"],
                    "environment": environment,
                    "secrets": sorted(secrets),
                    "command": [
                        "python",
                        "/deployment/deploy_flow.py",
                        "/deployment/flow_definition.json",
                        group_name,
                    ],
                    "restart": "no",
                }
            },
            "secrets": {
                target: {"environment": source}
                for target, source in secrets.items()
            },
        }
        rows = [
            {
                "sub": config_id,
                "pred": "tcs:literal",
                "obj": json.dumps(compose),
                "sub_type": URIRef,
                "obj_type": Literal,
            }
        ]
        body = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=self.output_reader.prefix_store,
        ).graph
        self.output_reader = self.output_reader.add(body)


_DEPLOY_SCRIPT = r'''
"""Upload a generated flow definition through the DSH gateway using stdlib only."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def request(url, *, token=None, data=None, content_type=None, method=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"{req.method} {url} failed: {error.code} {detail}") from error
    return json.loads(body) if body else None


def multipart(fields, filename, file_body):
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/json\r\n\r\n",
            file_body,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def json_request(url, token, *, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    return request(
        url,
        token=token,
        data=data,
        content_type="application/json" if data else None,
        method=method,
    )


def secret(name, *, required=False):
    path = Path("/run/secrets") / name
    if not path.exists():
        if required:
            raise RuntimeError(f"Missing deployment secret: {name}")
        return None
    return path.read_text().rstrip("\r\n")


def main(flow_path, group_name):
    required = ["DSH_GATEWAY_URL", "NIFI_BASE_URL"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing deployment properties: {', '.join(missing)}")

    token_data = urllib.parse.urlencode(
        {
            "username": secret("DSH_USERNAME", required=True),
            "password": secret("DSH_PASSWORD", required=True),
        }
    ).encode()
    token = request(
        os.environ["DSH_GATEWAY_URL"],
        data=token_data,
        content_type="application/x-www-form-urlencoded",
        method="POST",
    )["access_token"]
    base = os.environ["NIFI_BASE_URL"].rstrip("/")
    current_user = json_request(f"{base}/nifi-api/flow/current-user", token)
    print(f"Authenticated to NiFi as: {current_user['identity']}")

    parent_id = os.environ.get("NIFI_PARENT_PG_ID")
    if not parent_id:
        root = json_request(f"{base}/nifi-api/flow/process-groups/root", token)
        parent_id = root["processGroupFlow"]["id"]

    path = Path(flow_path)
    body, content_type = multipart(
        {
            "groupName": group_name,
            "positionX": 0,
            "positionY": 0,
            "clientId": uuid.uuid4(),
            "disconnectedNodeAcknowledged": "false",
        },
        path.name,
        path.read_bytes(),
    )
    created = request(
        f"{base}/nifi-api/process-groups/{parent_id}/process-groups/upload",
        token=token,
        data=body,
        content_type=content_type,
        method="POST",
    )
    group_id = created["id"]
    print(f"Created process group {created['component']['name']!r} with id {group_id}")

    account = secret("AZURE_STORAGE_ACCOUNT_NAME")
    sas_token = secret("AZURE_SAS_TOKEN")
    if account or sas_token:
        services = json_request(
            f"{base}/nifi-api/flow/process-groups/{group_id}/controller-services",
            token,
        )["controllerServices"]
        service = next(
            item
            for item in services
            if item["component"]["type"].endswith(
                ".AzureStorageCredentialsControllerService_v12"
            )
        )
        properties = {}
        if account:
            properties["Storage Account Name"] = account
        if sas_token:
            properties["SAS Token"] = sas_token
        json_request(
            f"{base}/nifi-api/controller-services/{service['id']}",
            token,
            method="PUT",
            body={
                "revision": service["revision"],
                "component": {"id": service["id"], "properties": properties},
            },
        )
    print("Import complete. The new process group remains stopped.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
'''
