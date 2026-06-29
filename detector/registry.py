import json, os

KNOWN_MEGA = {"BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6"}

class Registry:
    def __init__(self, path, mega_threshold=100):
        self.path = path
        self.mega_threshold = mega_threshold
        self.recipients = {}  # funder -> set(recipient)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.recipients = {k: set(v) for k, v in data.get("recipients", {}).items()}

    def is_mega_funder(self, addr):
        if addr in KNOWN_MEGA:
            return True
        return len(self.recipients.get(addr, ())) >= self.mega_threshold

    def note_funding(self, funder, recipient):
        if not funder or not recipient:
            return
        self.recipients.setdefault(funder, set()).add(recipient)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"recipients": {k: sorted(v) for k, v in self.recipients.items()}}, f)
        os.replace(tmp, self.path)
