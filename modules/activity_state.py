"""自动产生的本地发布记录与答案缓存；不是用户配置文件。"""
import hashlib
import json
from datetime import date
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class ActivityState:
    def __init__(self, path, scope):
        self.path = Path(path)
        self.scope = scope
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.records = self.data.setdefault(scope, {'posts': [], 'answers': {}})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def remaining(self, kind, limit):
        used = sum(p['kind'] == kind and p['day'] == date.today().isoformat() for p in self.records['posts'])
        return max(0, limit - used)

    def begin(self, kind, key, text, url, question=None):
        record = {'kind': kind, 'key': key, 'text': text, 'url': url,
                  'day': date.today().isoformat(), 'confirmed': False}
        if question:
            record['question'] = question
        self.records['posts'].append(record)
        self.save()  # 在点击发布之前落盘；崩溃或超时后也不会盲目重发。
        return record

    def confirm(self, record):
        record['confirmed'] = True
        self.save()

    def seen(self, kind, key):
        return any(p['kind'] == kind and p['key'] == key for p in self.records['posts'])
