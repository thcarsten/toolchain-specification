FROM maven:3.9-eclipse-temurin-21 AS nifi-extensions

ARG LDI_NIFI_VERSION=2.12.0

RUN mvn -B dependency:get \
    -Dartifact=be.vlaanderen.informatievlaanderen.ldes.ldi.nifi:ldi-processors-bundle:${LDI_NIFI_VERSION} \
&& mkdir -p /out/nifi-ext \
&& find /root/.m2/repository/be/vlaanderen/informatievlaanderen/ldes/ldi/nifi \
    -type f -name '*.nar' \
    -exec cp {} /out/nifi-ext/ \;

FROM apache/nifi:2.10.0

COPY --from=nifi-extensions --chown=nifi:nifi \
    /out/nifi-ext/ \
    /opt/nifi/nifi-current/nar_extensions/
