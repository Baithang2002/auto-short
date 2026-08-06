import json
from pathlib import Path

script_dir = Path(__file__).parent.parent.resolve()
topics_txt = script_dir / "topics.txt"
bank_file = script_dir / "state" / "topic_bank_state.json"

# Read fresh topics from topics.txt
raw_lines = topics_txt.read_text(encoding="utf-8").splitlines()
active_topics = []
for line in raw_lines:
    line = line.strip()
    if line and not line.startswith("#"):
        active_topics.append(line)

print(f"Read {len(active_topics)} active topics from topics.txt")

# Read existing bank state if it exists
if bank_file.exists():
    data = json.loads(bank_file.read_text(encoding="utf-8"))
else:
    data = {"version": 1, "updated_at": "", "records": []}

records = data.get("records", [])

# Build map of existing records by lowercase topic
existing = {r.get("topic", "").strip().lower(): r for r in records}

new_records = []
for topic in active_topics:
    key = topic.lower()
    if key in existing:
        rec = existing[key]
        # Reset quarantined or exhausted topics to candidate if they are in the new list
        if rec.get("status") in {"quarantined", "exhausted"}:
            rec["status"] = "candidate"
            rec["consecutive_failures"] = 0
            rec.pop("quarantined_until", None)
            rec.pop("last_error", None)
        new_records.append(rec)
    else:
        new_records.append({
            "topic": topic,
            "category": "wildlife",
            "status": "candidate",
            "consecutive_failures": 0,
            "added_at": "2026-08-02T22:15:00Z"
        })

data["records"] = new_records
data["updated_at"] = "2026-08-02T22:15:00Z"

bank_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"Successfully updated {bank_file}: total active candidate/proven records = {len(new_records)}")
