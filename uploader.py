import os
import git
import shutil
import datetime

# Token + URL sicher über Umgebungsvariablen
TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("REPO_URL")
REPO_AUTH_URL = REPO_URL.replace("https://", f"https://{TOKEN}@")
LOCAL_REPO_PATH = "/tmp/webhook-alarm-repo"

def git_upload(filename, local_source_path):
    # ggf. altes Repo löschen
    if os.path.exists(LOCAL_REPO_PATH):
        shutil.rmtree(LOCAL_REPO_PATH)

    # Repo klonen – SICHER!
    repo = git.Repo.clone_from(REPO_AUTH_URL, LOCAL_REPO_PATH)

    # Datei kopieren
    full_path = os.path.join(LOCAL_REPO_PATH, filename)
    shutil.copy(os.path.join(local_source_path, filename), full_path)

    # Commit & Push
    repo.git.add(filename)
    repo.index.commit(f"Upload {filename} – {datetime.datetime.now()}")
    origin = repo.remote(name='origin')
    origin.push()
