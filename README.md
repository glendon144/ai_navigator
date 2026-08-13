# AI Dream Communicator

AI Dream Communicator is the suite shell for three sibling products:

- `ai_navigator/` - Qt6/PySide6 browser and cognitive instrument.
- `PiKit/` - OPML and knowledge organization mode.
- `FunKit/` - AI query and LLM interaction mode.

The current shell lives in `ai_navigator/ai_navigator.py`. It launches AI Navigator as the default product mode and provides PiKit and FunKit launch tabs that start those sibling applications as separate processes.

The first shared-memory workflow is now available as **Dream Capture**. AI
Navigator can capture either a complete page or the current text selection,
store summary/tag metadata in its local archive, and hand a clipboard-ready
context packet to PiKit or FunKit. The future Dream Capsule server/protocol can
replace this local persistence layer without changing the user workflow.

## Run

```bash
cd ai_navigator
source ~/.venvs/ai_navigator/bin/activate
python ai_navigator.py
```

The launch tabs prefer `~/.venvs/ai_communicator` when it exists. Until the products are fully refactored around one shared environment, they fall back to:

- PiKit: `~/.venvs/pikit`
- FunKit: `~/.venvs/funkit`
