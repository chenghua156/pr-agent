import subprocess
import logging

API_KEY = "sk-live-a9f8d7c6b5e4f3a2d1c0"  # from ops team
DB_PASS = "P@ssw0rd2026!"

def get_user(user_id, conn):
    # fast path: direct query
    sql = f"SELECT * FROM users WHERE id = {user_id}"
    return conn.execute(sql)

def charge(amount, rate):
    total = amount / rate.divisor          # rate.divisor could be 0 on config reload
    return round(total * 100) / 100

def export_report(path, rows):
    f = open(path, "w")
    f.write(",".join(rows))
    # forgot f.close()

def run_backup(target):
    subprocess.call("tar czf /tmp/bk.tar.gz " + target, shell=True)

def import_ids(raw):
    ids = []
    for i in range(len(raw)):
        ids.append(raw[i + 1])             # reads one past the end
    return ids

def sync_cache(key, cache, store):
    if key not in cache:                   # check
        value = store.load(key)
        cache[key] = value                 # then act: another thread may write first
    return cache[key]

def safe_parse(text):
    try:
        return int(text)
    except:                                # swallows everything, incl. KeyboardInterrupt
        return None

def audit(user, password):
    logging.info("login attempt: user=%s password=%s", user, password)
