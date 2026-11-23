import random, itertools, sys
from math import isqrt
import sol

R = random.Random(7)
fails = 0


def chk(name, inp, fast, slow):
    global fails
    a, b = fast(inp), slow(inp)
    if a.split() != b.split():
        fails += 1
        if fails < 10:
            print('FAIL', name, repr(inp), a, b)


def b_groups(s):
    v = list(map(int, s.split())); n, A, B = v[:3]; r = sorted(v[3:])
    best = [None]
    def rec(i, cur):
        if i == n:
            if best[0] is None or cur < best[0]: best[0] = cur
            return
        for sz in range(A, B + 1):
            if i + sz <= n:
                rec(i + sz, max(cur, r[i + sz - 1] - r[i]))
    rec(0, 0)
    return str(best[0])


def b_tshirts(s):
    v = list(map(int, s.split())); n, m = v[:2]; sz = v[2:2 + n]
    segs = [(v[2 + n + 2 * i], v[3 + n + 2 * i]) for i in range(m)]
    best = 0
    for perm in itertools.permutations(range(n)):
        used = set(); c = 0
        # greedy over participants in each order of shirts assignment: try matching via brute
    # bipartite matching brute: Kuhn
    match = [-1] * n
    def tr(u, vis):
        for j in range(n):
            if segs[u][0] <= sz[j] <= segs[u][1] and j not in vis:
                vis.add(j)
                if match[j] == -1 or tr(match[j], vis):
                    match[j] = u; return True
        return False
    return str(sum(tr(u, set()) for u in range(m)))


def b_cube(s):
    v = list(map(int, s.split())); a = v[1:]
    def iscube(x):
        r = round(x ** (1 / 3))
        return any((r + d) ** 3 == x for d in (-1, 0, 1))
    return str(sum(iscube(a[i] * a[j]) for i in range(len(a)) for j in range(i + 1, len(a))))


def b_medals(s):
    v = list(map(int, s.split())); n, S = v[:2]
    it = [v[2 + 3 * i:5 + 3 * i] for i in range(n)]
    best = -1
    for x in it:
        for y in it:
            for z in it:
                if x[2] == 1 and y[2] == 2 and z[2] == 3 and x[0] + y[0] + z[0] <= S:
                    best = max(best, min(x[1], y[1], z[1]))
    return str(best)


def b_four(s):
    S, P = map(int, s.split()); c = 0
    for a in range(1, S + 1):
        for b in range(1, S + 1):
            for cc in range(1, S + 1):
                for d in range(1, S + 1):
                    if a + b + cc + d <= S and a * b * cc * d <= P: c += 1
    return str(c)


def b_nocarry(s):
    v = list(map(int, s.split())); a = v[1:]; best = 0
    for m in range(1 << len(a)):
        sel = [a[i] for i in range(len(a)) if m >> i & 1]
        ok = True
        for pos in range(10):
            if sum(x // 10 ** pos % 10 for x in sel) > 9: ok = False
        if ok: best = max(best, len(sel))
    return str(best)


def b_remove(s):
    n, k = s.split(); k = int(k)
    return str(max(int(''.join(c)) for c in itertools.combinations(n, len(n) - k)))


def b_pal(s):
    t = s.split(); n, m = int(t[0]), int(t[1]); w = t[2:]
    best = 0
    for r in range(1, n + 1):
        if r * m <= best: continue
        for comb in itertools.permutations(range(n), r):
            st = ''.join(w[i] for i in comb)
            if st == st[::-1]: best = max(best, len(st)); break
    return str(best)


def b_row(s):
    A, B, C = map(int, s.split())
    from functools import lru_cache
    @lru_cache(None)
    def f(a, b, c, last):
        r = 0
        for i, (x) in enumerate((a, b, c)):
            if x and i != last:
                cnt = [a, b, c]; cnt[i] -= 1
                r = max(r, 1 + f(*cnt, i))
        return r
    return str(f(A, B, C, -1))


def b_box(s):
    V = int(s); best = None
    for a in range(1, V + 1):
        if V % a: continue
        for b in range(1, V // a + 1):
            if (V // a) % b: continue
            c = V // a // b; sa = 2 * (a * b + b * c + a * c)
            best = sa if best is None else min(best, sa)
    return str(best)


def b_alikhan(s):
    v = list(map(int, s.split())); n = v[0]
    for x in range(3 * n + 3):
        if all((y + x) % n for y in v[1:]): return str(x)
    return '-1'


def b_split(s):
    v = list(map(int, s.split())); n = v[0]; a = v[1:]
    best = 0
    for m in range(1 << (n - 1)):
        parts, cur = [], [a[0]]
        for i in range(1, n):
            if m >> (i - 1) & 1: parts.append(cur); cur = []
            cur.append(a[i])
        parts.append(cur)
        res = [x for p in parts for x in sorted(p)]
        if res == sorted(a): best = max(best, len(parts))
    return str(best)


def b_icpc(s):
    v = list(map(int, s.split())); n = v[0]
    t = [v[1 + 3 * i:4 + 3 * i] for i in range(n)]
    best = (0, 0)
    for asg in itertools.product(range(4), repeat=n):
        pen, ok, cnt = 0, True, 0
        for p in range(3):
            ts = sorted(t[i][p] for i in range(n) if asg[i] == p)
            acc = 0
            for x in ts: acc += x; pen += acc
            if acc > 300: ok = False
            cnt += len(ts)
        if ok and (cnt > best[0] or cnt == best[0] and pen < best[1]): best = (cnt, pen)
    return f'{best[0]} {best[1]}'


def b_triples(s):
    v = list(map(int, s.split())); a = v[1:]; n = len(a)
    return str(sum(1 for i in range(n) for j in range(n) for k in range(n)
                   if len({i, j, k}) == 3 and a[i] * a[j] == a[k] ** 2))


def b_iso(s):
    N = int(s); c = 0
    for x in range(1, N + 1):
        for y in range(1, N + 1):
            if y < 2 * x: c += 1
    return str(c)


def b_even(s):
    L, R_ = map(int, s.split())
    return str(sum(1 for x in range(L, R_ + 1) if all(int(d) % 2 == 0 for d in str(x))))


def b_rest(s):
    v = list(map(int, s.split())); n, m = v[:2]
    W = [v[2 + 3 * i:5 + 3 * i] for i in range(n)]; a = v[2 + 3 * n:]
    res = []
    for sk in range(n):
        g = [0] * m
        for i, (l, r, x) in enumerate(W):
            if i != sk:
                for j in range(l - 1, r): g[j] += x
        if g == a: res.append(sk + 1)
    return ' '.join(map(str, res))


def b_trav(s):
    n = int(s); from collections import deque
    dist = {0: 0}; q = deque([0])
    while q:
        u = q.popleft()
        for w in (2 * u, 2 * u + 1):
            if w <= n and w not in dist: dist[w] = dist[u] + 1; q.append(w)
    return str(dist[n])


def b_towers(s):
    v = list(map(int, s.split())); s1, s2, s3 = v[:3]
    t = [v[3:3 + s1], v[3 + s1:3 + s1 + s2], v[3 + s1 + s2:]]
    best = 0
    for i in range(s1 + 1):
        for j in range(s2 + 1):
            for k in range(s3 + 1):
                if sum(t[0][:i]) == sum(t[1][:j]) == sum(t[2][:k]): best = max(best, sum(t[0][:i]))
    return str(best)


for _ in range(300):
    n = R.randint(2, 9); A = R.randint(2, n); B = R.randint(A, n)
    # гарантировать разбиение: n должно представляться суммой размеров из [A,B]
    ok = any(n % k == 0 or True for k in range(A, B + 1))
    can = [False] * (n + 1); can[0] = True
    for i in range(1, n + 1):
        can[i] = any(can[i - k] for k in range(A, B + 1) if i - k >= 0)
    if not can[n]: continue
    chk('groups', f"{n} {A} {B}\n" + ' '.join(str(R.randint(1, 20)) for _ in range(n)), sol.groups, b_groups)
for _ in range(300):
    n, m = R.randint(1, 6), R.randint(1, 6)
    segs = []
    for _ in range(m):
        l = R.randint(1, 15); segs.append(f"{l} {R.randint(l, 15)}")
    chk('tshirts', f"{n} {m}\n" + ' '.join(str(R.randint(1, 15)) for _ in range(n)) + '\n' + '\n'.join(segs), sol.tshirts, b_tshirts)
for _ in range(200):
    n = R.randint(1, 12)
    pool = [1, 2, 4, 8, 16, 27, 54, 3, 9, 12, 18, 36, 1000003, 1000003 ** 2 if False else 2, 1009 * 1013, 1009, 1009 ** 2, 2 * 1009, 4 * 1009 ** 2, 1009 * 1013 * 7]
    chk('cube', f"{n}\n" + ' '.join(str(R.choice(pool + [R.randint(1, 200)])) for _ in range(n)), sol.cube, b_cube)
for _ in range(300):
    n = R.randint(3, 8)
    it = [f"{R.randint(1, 10)} {R.randint(1, 10)} {c}" for c in [1, 2, 3] + [R.randint(1, 3) for _ in range(n - 3)]]
    R.shuffle(it)
    chk('medals', f"{n} {R.randint(3, 30)}\n" + '\n'.join(it), sol.medals, b_medals)
for S in range(1, 14):
    for P in (1, 2, 5, 10, 17, 50, 1000):
        chk('four', f"{S} {P}", sol.four_numbers, b_four)
for _ in range(200):
    n = R.randint(1, 8)
    chk('nocarry', f"{n}\n" + ' '.join(str(R.choice([R.randint(1, 9), R.randint(1, 999), R.randint(1, 10 ** 8)])) for _ in range(n)), sol.no_carry, b_nocarry)
for _ in range(300):
    n = str(R.randint(1, 10 ** R.randint(1, 9)))
    chk('remove', f"{n}\n{R.randint(0, len(n) - 1)}", sol.remove_digits, b_remove)
for _ in range(150):
    n, m = R.randint(1, 6), R.randint(1, 3)
    al = 'ab'
    ws = [''.join(R.choice(al) for _ in range(m)) for _ in range(n)]
    chk('pal', f"{n} {m}\n" + '\n'.join(ws), sol.palindrome, b_pal)
for a in range(1, 7):
    for b in range(1, 7):
        for c in range(1, 7):
            chk('row', f"{a}\n{b}\n{c}", sol.olymp_row, b_row)
for V in range(1, 400):
    chk('box', str(V), sol.box, b_box)
for _ in range(300):
    n = R.randint(1, 8)
    chk('alikhan', f"{n}\n" + ' '.join(str(R.randint(1, n)) for _ in range(n)), sol.alikhan, b_alikhan)
for _ in range(300):
    n = R.randint(1, 9)
    chk('split', f"{n}\n" + ' '.join(str(R.randint(1, 6)) for _ in range(n)), sol.split_sort, b_split)
for _ in range(60):
    n = R.randint(1, 6)
    chk('icpc', f"{n}\n" + '\n'.join(' '.join(str(R.randint(1, R.choice([60, 200, 500]))) for _ in range(3)) for _ in range(n)), sol.icpc, b_icpc)
for _ in range(150):
    n = R.randint(3, 12)
    chk('triples', f"{n}\n" + ' '.join(map(str, R.sample(range(1, 60), n))), sol.triples, b_triples)
for N in range(1, 60):
    chk('iso', str(N), sol.isosceles, b_iso)
for _ in range(300):
    L = R.randint(1, 3000); chk('even', f"{L} {R.randint(L, 3000)}", sol.even_digits, b_even)
for _ in range(300):
    n, m = R.randint(1, 5), R.randint(1, 5)
    W = []
    for _ in range(n):
        l = R.randint(1, m); W.append((l, R.randint(l, m), R.randint(1, 3)))
    sk = R.randrange(n); g = [0] * m
    for i, (l, r, x) in enumerate(W):
        if i != sk:
            for j in range(l - 1, r): g[j] += x
    chk('rest', f"{n} {m}\n" + '\n'.join(f"{l} {r} {x}" for l, r, x in W) + '\n' + ' '.join(map(str, g)), sol.restaurant, b_rest)
for n in range(1, 3000):
    chk('trav', str(n), sol.traveler, b_trav)
for _ in range(200):
    s = [R.randint(1, 5) for _ in range(3)]
    chk('towers', ' '.join(map(str, s)) + '\n' + '\n'.join(' '.join(str(R.randint(1, 4)) for _ in range(k)) for k in s), sol.towers, b_towers)
print('fails', fails)
