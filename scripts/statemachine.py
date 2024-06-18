from airlock.business_logic import BusinessLogicLayer


initial = False

lines = []
for current, valid in BusinessLogicLayer.VALID_STATE_TRANSITIONS.items():
    if not initial:
        lines.append(f"[*] --> {current.value}")
        initial = True
    for state in valid:
        lines.append(f"{current.value} --> {state.value}")

diagram = "\n    ".join(lines)

print(f"""
# State machine diagram for RequestStatus

Generated from `BusinessLogicLayer.VALID_STATE_TRANSITIONS` by scripts/statemachine.py

```mermaid
stateDiagram-v2
    {diagram}
```
""")
