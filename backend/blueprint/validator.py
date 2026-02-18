REQUIRED = ["Summons", "Caption", "Verification"]


def validate(blueprint):

    names = [s["name"] for s in blueprint["sections"]]

    for r in REQUIRED:
        if r not in names:
            raise Exception(f"Missing {r}")
