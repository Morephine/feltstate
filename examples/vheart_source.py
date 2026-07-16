"""examples/vheart_source.py — use a fine-tuned LoRA adapter as the affect source.

This swaps the default :class:`KeywordSource` for :class:`VheartSource`, which
loads a LoRA adapter from the Hub and asks it to *estimate* the character's
reaction. Same Engine, same state — the source is the only difference. Point
``adapter`` at your own Hub repo, and pin ``revision`` to a commit SHA for
reproducible loads.

Install requirements::

    pip install "feltstate[vheart]"

(brings in torch, transformers, peft, huggingface_hub).
"""

from feltstate import Engine
from feltstate.sources.vheart import VheartSource


def main() -> None:
    # Use one of the illustrative adapters shipped alongside the library.
    # Pin `revision="<commit-sha>"` in real use so a later push to the repo
    # can't silently change the weights you load.
    src = VheartSource("kaishuiji/vheart-affect-v9")
    eng = Engine(source=src)

    eng.tick([{"role": "user", "content": "今晚跑通了三周的实验。"}])
    print("after success:", eng.state.mood)
    print("  mixed_blend:", eng.state.mood.mixed_blend)

    eng.tick([{"role": "user", "content": "... but no one in the meeting noticed."}])
    print("after letdown:", eng.state.mood)

    eng.tick([{"role": "user", "content": "It's fine. Onto the next one."}])
    print("after move-on:", eng.state.mood)


if __name__ == "__main__":
    main()
