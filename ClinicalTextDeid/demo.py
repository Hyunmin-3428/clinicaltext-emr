"""Print only synthetic transformed text and restoration verdict; keys stay in RAM."""
import os
from .api import transform, restore


def main():
    source = "47-year-old John Smith, professor at Yonsei University, ran Seoul Marathon last Sunday, father died from MI at 52."
    context = {"project": "synthetic", "document": "example-1", "release": "local-test", "purpose": "research"}
    key = os.urandom(32)
    for policy in [{"mode": "direct"}, {"mode": "context_rules"},
                   {"mode": "context_rules", "preserve_numeric_age": False, "preserve_exercise_time": False}]:
        result = transform({"text": source, "context": context, "policy": policy}, key=key)
        print(result["public"]["text"])
        print("byte-exact restore:", restore(result, key=key, context=context) == source.encode("utf-8"))


if __name__ == "__main__":
    main()
