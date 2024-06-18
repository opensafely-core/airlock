
# State machine diagram for RequestStatus

Generated from `BusinessLogicLayer.VALID_STATE_TRANSITIONS` by scripts/statemachine.py

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> SUBMITTED
    PENDING --> WITHDRAWN
    SUBMITTED --> APPROVED
    SUBMITTED --> REJECTED
    SUBMITTED --> RETURNED
    SUBMITTED --> WITHDRAWN
    RETURNED --> SUBMITTED
    RETURNED --> WITHDRAWN
    APPROVED --> RELEASED
    REJECTED --> APPROVED
```

