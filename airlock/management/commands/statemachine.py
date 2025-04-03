from pathlib import Path

from django.core.management.base import BaseCommand

from airlock.business_logic import BusinessLogicLayer


DIAGRAM_OUTPUT_TEMPLATE = """
# State machine diagram for RequestStatus

Generated from `BusinessLogicLayer.VALID_STATE_TRANSITIONS` by scripts/statemachine.py

```mermaid
stateDiagram-v2
    {diagram}
```

"""


class Command(BaseCommand):
    """
    Generate state machine diagram
    """

    def add_arguments(self, parser):
        parser.add_argument("output_path", type=Path)

    def handle(self, *args, **options):
        output_path = options["output_path"]

        initial = False

        lines = []
        for current, valid in BusinessLogicLayer.VALID_STATE_TRANSITIONS.items():
            if not initial:
                lines.append(f"[*] --> {current.value}")
                initial = True
            for state in valid:
                lines.append(f"{current.value} --> {state.value}")

        diagram = "\n    ".join(lines)

        output = DIAGRAM_OUTPUT_TEMPLATE.format(diagram=diagram)

        output_path.write_text(output)
