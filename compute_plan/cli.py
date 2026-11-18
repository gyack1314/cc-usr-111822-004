"""读取业务资料并展示核算结果:默认月度电费,可选情景核算与方案对比。"""
import argparse
import json
from pathlib import Path
from .evidence import connect, receive
from .billing import month
from .scenario import evaluate
from .compare import compare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('--scenario', metavar='PLAN', help='评估指定扩容方案')
    parser.add_argument('--compare', action='store_true', help='按输入 compare 配置对比已批准与新方案')
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding='utf-8'))
    db = connect()
    try:
        for item in data['documents']:
            receive(db, item)
        if args.compare:
            cfg = data['compare']
            out = compare(db, cfg['approved'], cfg['candidate'], cfg['before'], cfg['after'])
        elif args.scenario:
            out = evaluate(db, args.scenario, data['known_at'])
        else:
            out = month(db, data['known_at'])
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == '__main__':
    main()
