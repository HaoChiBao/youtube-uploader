from dotenv import load_dotenv

load_dotenv()

from uploader.object_storage import list_keys, storage_bucket

bucket = storage_bucket()
prefixes = [
    "",
    "youtuber-uploader/",
    "queue/",
    "youtuber-uploader/queue/",
    "config/",
    "youtuber-uploader/config/",
    "secrets/",
    "youtuber-uploader/secrets/",
    "state/",
    "youtuber-uploader/state/",
    "uploaded/",
    "youtuber-uploader/uploaded/",
]

for p in prefixes:
    if p:
        uri = f"s3://{bucket}/" + p
    else:
        uri = f"s3://{bucket}/config/"  # probe via known prefix at root
    keys = list_keys(uri) if p else (
        list_keys(f"s3://{bucket}/config/")
        + list_keys(f"s3://{bucket}/secrets/")
        + list_keys(f"s3://{bucket}/state/")
        + list_keys(f"s3://{bucket}/queue/")
        + list_keys(f"s3://{bucket}/uploaded/")
    )
    label = p or "(bucket root)"
    print(f"{label}: {len(keys)} keys")
    for k in sorted(keys)[:20]:
        short = k.replace(f"s3://{bucket}/", "") if k.startswith("s3://") else k
        print(f"  {short}")
    if len(keys) > 20:
        print(f"  ... +{len(keys) - 20} more")
    print()
