import gzip, json, random, sys, time
import data

out = []
total_bytes = 0
for ci, c in enumerate(data.CONTESTS):
    year = c['title'].split()[2]          # 2025-2026
    short = year[:5] + year[-2:]          # 2025-26
    tasks = []
    for t in c['tasks']:
        R = random.Random(f"{c['title']}/{t['name']}")
        gen = t['gen'](R)
        need = 10 - len(t['samples'])
        if len(gen) < need:
            sys.exit(f"{t['name']}: мало тестов {len(gen)} < {need}")
        gen = gen[:need]
        tests = []
        for inp, o in t['samples']:
            got = t['solve'](inp)
            assert got.split() == o.split(), (t['name'], got, o)
            tests.append({'input': inp + "\n", 'output': o})
        worst = 0
        for inp in gen:
            t0 = time.time()
            o = t['solve'](inp)
            worst = max(worst, time.time() - t0)
            tests.append({'input': inp + "\n", 'output': o})
        ins = [x['input'] for x in tests]
        assert len(set(ins)) == len(ins), f"{t['name']}: повтор тестов"
        outs = [x['output'] for x in tests]
        size = sum(len(x['input']) + len(x['output']) for x in tests)
        total_bytes += size
        print(f"{short} {t['letter']} {t['name']:<38} {size/1024:8.0f} KB  ref {worst:5.2f}s  разных ответов {len(set(outs))}", file=sys.stderr)
        level = {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'C'}[t['letter']]
        s0 = t['samples'][0]
        tasks.append({
            'title': f"{t['letter']}. {t['name']} · {short}",
            'level': level,
            'description': t['description'],
            'input_example': s0[0], 'output_example': s0[1],
            'examples': [{'input': i, 'output': o} for i, o in t['samples']],
            'time_limit': t['time_limit'],
            'tags': [],
            'hidden': True,
            'test_cases': tests,
        })
    out.append({'title': c['title'], 'date': c['date'], 'tasks': tasks})

raw = json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode()
with gzip.open(sys.argv[1], 'wb', compresslevel=9) as f:
    f.write(raw)
print(f"итого {total_bytes/1024/1024:.1f} MB сырых, файл {len(gzip.compress(raw, 9))/1024/1024:.2f} MB", file=sys.stderr)
