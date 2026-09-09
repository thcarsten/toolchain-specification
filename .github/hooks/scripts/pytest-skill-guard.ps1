$ErrorActionPreference = 'Stop'
$input_json = [Console]::In.ReadToEnd() | ConvertFrom-Json

if ($input_json.tool_name -ne 'run_in_terminal') {
    exit 0
}

$cmd = $input_json.tool_input.command
if ($null -eq $cmd -or $cmd -notmatch 'pytest') {
    exit 0
}

# Raw pytest invocation without output redirection risks dumping verbose logs into context.
if ($cmd -notmatch '>') {
    $result = @{
        hookSpecificOutput = @{
            hookEventName            = 'PreToolUse'
            permissionDecision        = 'deny'
            permissionDecisionReason  = "Raw 'pytest' calls are blocked. Follow the run-pipeline-generator-tests skill (.github/skills/run-pipeline-generator-tests/SKILL.md): redirect output to a file and run in async mode, only after explicit user permission."
        }
    }
    $result | ConvertTo-Json -Depth 5
}
exit 0
