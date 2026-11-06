<p align="center"><img src="assets/banner.png" alt="hcawn_ha" width="100%"></p>

# hcawn_ha

**A 5.7M-parameter model that turns spoken Home Assistant commands into service calls.** 6.0 MB (int8) or 4.1 MB (int4), runs on a CPU, no cloud.

This repository holds everything around the model: the **resolver** (maps what you said to your devices), the **pipeline** (command splitting, confidence gate, safety rules), a **local demo house**, and the **data recipes**. The weights live on Hugging Face: [datatab/hcawn-ha](https://huggingface.co/datatab/hcawn-ha). Live demo: [datatab/hcawn-ha-demo](https://huggingface.co/spaces/datatab/hcawn-ha-demo).

## How it works

```
"turn off the kitchen light and lock the front door"
        │
        ▼  split into commands
        ▼  model (per command, with confidence)   →  {"name":"HassTurnOff","arguments":{"name":"kitchen light"}}
        ▼  direction guard + validation
        ▼  resolver (your device registry, rooms, aliases)
        ▼  Home Assistant service calls    light.turn_off → light.kitchen_ceiling
                                           lock.lock      → lock.front_door   (asks for a "yes" first)
```

- The model never sees your device list. It copies the device phrase from the request; the resolver finds the entity.
- Nothing runs when the model is unsure (confidence gate), when the verb contradicts the tool, or when the device is ambiguous. In that case it asks.
- Locks, garage doors and gates always wait for a confirmation.
- 13 tools: turn on / off / toggle, light brightness and colour, thermostat, humidifier, shopping list, vacuum, media pause / resume / next / previous.

## Results

| | model alone | end to end (split + resolver) | official HA intents tests | acon96 V2 test |
|---|---|---|---|---|
| int8 (6.0 MB) | 95.0 % | 92.6 % | 92.4 % right, 0.7 % wrong | 89.7 % right, 1.8 % wrong |
| int4 (4.1 MB) | 94.8 % | 92.6 % | 91.0 % right, 2.1 % wrong | 89.1 % right, 1.4 % wrong |

With the confidence gate at 0.7 the wrong-action rate drops further (int8: 0.0 % on the official tests) at the price of asking more often. Part of the official-test result was tuned after looking at failures; the acon96 numbers are the independent ones. Details are on the model card.

## Quick start

The inference engine ships compiled inside the public [`hcawn-fc`](https://pypi.org/project/hcawn-fc/) wheel (Linux x86-64). Python 3.9+.

```bash
pip install hcawn-fc huggingface_hub
hf download datatab/hcawn-ha int8.bin int4.bin --local-dir demo
python demo/app.py            # opens http://127.0.0.1:8765
```

The demo is a simulated house you control by typing. It shows the raw model JSON, the confidence, the matched device and what changed; int8 / int4 can be switched live and compared side by side. You can add your own devices.

Use the pipeline from your own code:

```python
import sys; sys.path.insert(0, "hcawn_ha")
from ha_pipeline import HAPipeline

registry = [{"entity_id": "light.kitchen_ceiling", "name": "Kitchen Ceiling Light", "area": "Kitchen", "aliases": [], "device_class": None}]
pipe = HAPipeline("demo/int8.bin", registry, gate=0.7)
r = pipe.handle("turn on the kitchen light")
print(r["execute"], r["confirm"], r["ask"])
```

`execute` is the list of service calls that are safe to run now, `confirm` holds calls that need a spoken "yes", `ask` is a question for the user when something was unclear.

## Layout

| Folder | What is in it |
|---|---|
| `hcawn_ha/` | `ha_resolver.py` (name → entity), `ha_pipeline.py` (the pipeline), `shim.py` (glue to the engine), `ha_tools.json` (the 13 tools) |
| `demo/` | local demo house (`app.py`, `house.py`, `index.html`) |
| `tests/` | resolver tests; run `python tests/test_resolver.py` and `python hcawn_ha/ha_pipeline.py --selftest` (no weights needed) |
| `data_tools/` | how the training data was built: conversion of Home-Assistant-Requests-V2, LLM paraphrases, templates, mixing |
| `assets/` | images |

## Data

Training data is built from [Home-Assistant-Requests-V2](https://huggingface.co/datasets/acon96/Home-Assistant-Requests-V2) by acon96 (MIT), LLM paraphrases of it, and template-generated requests for locks, gates, whole rooms and telegraphic phrasing. `data_tools/` contains the scripts, not the data files.

## License

Code in this repository: MIT. Model weights: see the Hugging Face model card. The inference engine in the `hcawn-fc` wheel is distributed as a compiled library; its source is not part of this repository.
