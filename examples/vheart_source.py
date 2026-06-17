"""examples/vheart_source.py — use a fine-tuned LoRA adapter as the affect source.

This swaps the default :class:`KeywordSource` for :class:`VheartSource`, which
loads a LoRA adapter from the Hub (``kaishuiji/vheart-affect-v9`` here) and
asks it to *estimate* the character's reaction. Same Engine, same state —
the source is the only difference.

Install requirements::

    pip install "feltstate[vheart]"

(brings in torch, transformers, peft, huggingface_hub).
"""

from feltstate import Engine
from feltstate.sources.vheart import VheartSource


def main() -> None:
    src = VheartSource("kaishuiji/vheart-affect-v9")
    eng = Engine(source=src)

    eng.observe("I just nailed the demo. Three weeks of work — paid off.")
    print("after success:", eng.state.mood)
    print("  mixed_blend:", eng.state.mood.mixed_blend)

    eng.observe("... but no one in the meeting noticed.")
    print("after letdown:", eng.state.mood)

    eng.observe("It's fine. Onto the next one.")
    print("after move-on:", eng.state.mood)


if __name__ == "__main__":
    main()
