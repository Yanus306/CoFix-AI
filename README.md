# CoFix AI

## Protobuf setup

The protobuf schemas are stored in the `proto` Git submodule. Clone the project
with the submodule included:

```powershell
git clone --recurse-submodules https://github.com/Yanus306/CoFix-AI.git
```

For an existing clone, initialize or update the schemas with:

```powershell
git submodule update --init --recursive
```

Regenerate the checked-in Python bindings after a schema update:

```powershell
.\.venv\Scripts\python.exe scripts\generate_protos.py
```

Application code imports generated modules from `generated_proto`. The `proto`
directory must contain only the six `.proto` schema files and Git submodule
metadata.
