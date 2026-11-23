"""Эталонные решения 31 задачи районных олимпиад. Каждая: solve_X(input_text) -> output_text."""
import bisect
import heapq
from math import isqrt


def ints(s):
    return list(map(int, s.split()))


# ---------------- 2025-26
def animals(s):
    L, H = ints(s)
    cows = H // 2
    return f"{cows} {(L - 4 * cows) // 4}"


def table_area(s):
    v = ints(s)
    n = v[0]
    row, col = v[1:1 + n], v[1 + n:1 + 2 * n]
    a11 = row[0]
    return "\n".join(" ".join(str(row[j] * col[i] // a11) for j in range(n)) for i in range(n))


def groups(s):
    v = ints(s)
    n, A, B = v[:3]
    r = sorted(v[3:3 + n])

    def ok(D):
        can = [False] * (n + 1)
        pre = [0] * (n + 2)          # pre[k] = число True среди can[0..k-1]
        can[0] = True
        pre[1] = 1
        for i in range(1, n + 1):
            lo = bisect.bisect_left(r, r[i - 1] - D)   # первый j с r[j] >= r[i-1]-D
            a, b = max(i - B, lo), i - A
            if b >= a and b >= 0 and pre[b + 1] - pre[max(a, 0)] > 0:
                can[i] = True
            pre[i + 1] = pre[i] + can[i]
        return can[n]

    lo, hi = 0, r[-1] - r[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid + 1
    return str(lo)


# ---------------- 2024-25
def three_numbers(s):
    a, b, c, d = ints(s)
    v = [a, b, c]
    v.remove(d)
    return str(v[0] * v[1])


def traveler(s):
    return str(int(s.split()[0]).bit_length())


def tshirts(s):
    v = ints(s)
    n, m = v[:2]
    sz = sorted(v[2:2 + n])
    segs = sorted((v[2 + n + 2 * i], v[3 + n + 2 * i]) for i in range(m))
    heap, k, ans = [], 0, 0
    for x in sz:
        while k < m and segs[k][0] <= x:
            heapq.heappush(heap, segs[k][1]); k += 1
        while heap and heap[0] < x:
            heapq.heappop(heap)
        if heap:
            heapq.heappop(heap); ans += 1
    return str(ans)


SMALL_PRIMES = [p for p in range(2, 1001) if all(p % q for q in range(2, isqrt(p) + 1))]


def _cube_key(x):
    sig, comp = 1, 1
    for p in SMALL_PRIMES:
        if x % p == 0:
            e = 0
            while x % p == 0:
                x //= p; e += 1
            e %= 3
            if e:
                sig *= p ** e
                comp *= p ** (3 - e)
    # остаток x: 1, p, p^2 или p*q (простые > 1000), либо простое < 10^6 после раннего выхода
    if x == 1:
        big, bigc = (0, 1), (0, 1)
    else:
        r = isqrt(x)
        if r * r == x:
            big, bigc = (2, r), (1, r)
        else:
            big, bigc = (1, x), (2, x)
    return (sig, big), (comp, bigc)


def cube(s):
    v = ints(s)
    n = v[0]
    cnt, ans = {}, 0
    for x in v[1:1 + n]:
        key, comp = _cube_key(x)
        ans += cnt.get(comp, 0)
        cnt[key] = cnt.get(key, 0) + 1
    return str(ans)


# ---------------- 2023-24
def sockets(s):
    a, b = ints(s)
    return str(a + 2 * b)


def coach(s):
    v = ints(s)
    n = v[0]
    p = sorted(v[1:1 + 2 * n])
    return f"{sum(p[n:])} {sum(p[:n])}"


def towers(s):
    v = ints(s)
    s1, s2, s3 = v[:3]
    t = [v[3:3 + s1], v[3 + s1:3 + s1 + s2], v[3 + s1 + s2:3 + s1 + s2 + s3]]
    sets = []
    for tw in t:
        acc, st = 0, {0}
        for h in tw:
            acc += h; st.add(acc)
        sets.append(st)
    return str(max(sets[0] & sets[1] & sets[2]))


def medals(s):
    v = ints(s)
    n, S = v[:2]
    items = sorted(((v[2 + 3 * i + 1], v[2 + 3 * i], v[2 + 3 * i + 2]) for i in range(n)), reverse=True)
    INF = 10 ** 18
    best = [INF] * 4
    for b, a, c in items:
        best[c] = min(best[c], a)
        if best[1] + best[2] + best[3] <= S:
            return str(b)
    return "-1"


# ---------------- 2022-23
def four_numbers(s):
    import numpy as np
    S, P = ints(s)
    total = 0
    for a in range(1, S + 1):
        if a + 3 > S or a > P:
            break
        for b in range(1, S - a - 1):
            ab = a * b
            if ab > P:
                break
            cmax = S - a - b - 1
            c = np.arange(1, cmax + 1, dtype=np.int64)
            abc = ab * c
            c = c[abc <= P]
            if c.size == 0:
                break
            d = np.minimum(S - a - b - c, P // (ab * c))
            total += int(d[d > 0].sum())
    return str(total)


def no_carry(s):
    v = ints(s)
    n = v[0]
    a = v[1:1 + n]
    packed = []
    for x in a:
        p, sh = 0, 0
        while x:
            p |= (x % 10) << sh; x //= 10; sh += 8
        packed.append(p)
    HIGH = int('80' * 10, 16)
    ADD = int('76' * 10, 16)
    best = 0
    sums = [0] * (1 << n)
    valid = bytearray(1 << n)
    valid[0] = 1
    cnt = [0] * (1 << n)
    for m in range(1, 1 << n):
        low = m & -m
        i = low.bit_length() - 1
        prev = m ^ low
        if not valid[prev]:
            continue
        sm = sums[prev] + packed[i]
        if (sm + ADD) & HIGH:
            continue
        valid[m] = 1
        sums[m] = sm
        cnt[m] = cnt[prev] + 1
        if cnt[m] > best:
            best = cnt[m]
    return str(best)


# ---------------- 2021-22, 11
def two_friends(s):
    v = ints(s)
    p, q = v[:3], v[3:6]
    return str(min(max(p[i], q[j]) for i in range(3) for j in range(3) if i != j))


def remove_digits(s):
    n, k = s.split()[:2]
    k = int(k)
    st = []
    for ch in n:
        while k and st and st[-1] < ch:
            st.pop(); k -= 1
        st.append(ch)
    if k:
        st = st[:-k]
    return "".join(st)


def palindrome(s):
    tok = s.split()
    n, m = int(tok[0]), int(tok[1])
    cnt = {}
    for w in tok[2:2 + n]:
        cnt[w] = cnt.get(w, 0) + 1
    pairs, center = 0, 0
    for w, c in cnt.items():
        r = w[::-1]
        if r == w:
            pairs += c // 2
            if c % 2:
                center = 1
        elif w < r and r in cnt:
            pairs += min(c, cnt[r])
    return str((2 * pairs + center) * m)


# ---------------- 2021-22, 10
def olymp_row(s):
    v = sorted(ints(s))
    rest = v[0] + v[1]
    return str(sum(v) if v[2] <= rest + 1 else 2 * rest + 1)


def _factor(n):
    f = {}
    d = 2
    while d * d <= n:
        while n % d == 0:
            f[d] = f.get(d, 0) + 1; n //= d
        d += 1 if d == 2 else 2
    if n > 1:
        f[n] = f.get(n, 0) + 1
    return f


def _divisors(f):
    ds = [1]
    for p, e in f.items():
        ds = [d * p ** k for d in ds for k in range(e + 1)]
    return sorted(ds)


def box(s):
    V = int(s.split()[0])
    ds = _divisors(_factor(V))
    best = None
    for a in ds:
        if a * a * a > V:
            break
        W = V // a
        for b in ds:
            if b < a:
                continue
            if b * b > W:
                break
            if W % b:
                continue
            c = W // b
            sa = 2 * (a * b + b * c + a * c)
            if best is None or sa < best:
                best = sa
    return str(best)


def alikhan(s):
    v = ints(s)
    n = v[0]
    bad = {(-x) % n for x in v[1:1 + n]}
    for x in range(n):
        if x not in bad:
            return str(x)
    return "-1"


# ---------------- 2021-22, 9
def blue_yellow(s):
    a, b, c, d = ints(s)
    return str(min(a, c) * min(b, d))


def xyz(s):
    x, y, z = ints(s)
    a = isqrt(x * z // y)
    return f"{a} {x // a} {z // a}"


def split_sort(s):
    v = ints(s)
    n = v[0]
    a = v[1:1 + n]
    suf = [0] * (n + 1)
    suf[n] = 10 ** 18
    for i in range(n - 1, -1, -1):
        suf[i] = min(suf[i + 1], a[i])
    mx, ans = 0, 0
    for i in range(n):
        mx = max(mx, a[i])
        if mx <= suf[i + 1]:
            ans += 1
    return str(ans)


# ---------------- 2020-21
def olymp_mult(s):
    a, b, c, d = ints(s)
    return str(a * b * c * d)


def happy(s):
    k = int(s.split()[0])
    return str((10 * k - 5) ** 2)


def icpc(s):
    v = ints(s)
    n = v[0]
    t = [[v[1 + 3 * i + p] for i in range(n)] for p in range(3)]
    INF = float('inf')
    N = 1 << n
    f = []
    for p in range(3):
        fp = [INF] * N
        for m in range(N):
            ts = sorted(t[p][i] for i in range(n) if m >> i & 1)
            if sum(ts) <= 300:
                acc = pen = 0
                for x in ts:
                    acc += x; pen += acc
                fp[m] = pen
        f.append(fp)

    def combine(f1, f2):
        g = [INF] * N
        for m in range(N):
            sub = m
            while True:
                val = f1[sub] + f2[m ^ sub]
                if val < g[m]:
                    g[m] = val
                if sub == 0:
                    break
                sub = (sub - 1) & m
        return g

    g = combine(f[0], combine(f[1], f[2]))
    best = (0, 0)
    for m in range(N):
        if g[m] < INF:
            c = bin(m).count('1')
            if c > best[0] or (c == best[0] and g[m] < best[1]):
                best = (c, g[m])
    return f"{best[0]} {best[1]}"


# ---------------- 2019-20, 2 тур
def divisors_even(s):
    n = int(s.split()[0])
    return str(n - isqrt(n))


def second_max(s):
    v = ints(s)
    n = v[0]
    a = v[1:1 + n]
    m1, m2 = max(a[0], a[1]), min(a[0], a[1])
    out = [m2]
    for x in a[2:]:
        if x > m1:
            m1, m2 = x, m1
        elif x > m2:
            m2 = x
        out.append(m2)
    return " ".join(map(str, out))


def triples(s):
    v = ints(s)
    n = v[0]
    a = v[1:1 + n]
    present = set(a)
    MAXV = max(a)
    spf = list(range(MAXV + 1))
    for i in range(2, isqrt(MAXV) + 1):
        if spf[i] == i:
            for j in range(i * i, MAXV + 1, i):
                if spf[j] == j:
                    spf[j] = i
    ans = 0
    for x in a:
        f, y = {}, x
        while y > 1:
            p = spf[y]; f[p] = f.get(p, 0) + 2; y //= p
        sq = x * x
        for d in _divisors(f):
            if d >= x:
                break
            if d in present and sq // d <= MAXV and sq // d in present:
                ans += 2
    return str(ans)


# ---------------- 2019-20, 1 тур
def isosceles(s):
    N = int(s.split()[0])
    # sum_{x=1..N} min(N, 2x-1)
    k = min(N, (N + 1) // 2)        # x с 2x-1 <= N
    return str(k * k + (N - k) * N)


def _even_upto(X):
    """Количество чисел 1..X, у которых все цифры чётные."""
    if X <= 0:
        return 0
    ds = str(X)
    L = len(ds)
    total = 0
    for l in range(1, L):
        total += 4 * 5 ** (l - 1)
    # длины L, <= X
    for i, ch in enumerate(ds):
        d = int(ch)
        choices = [e for e in (0, 2, 4, 6, 8) if e < d and not (i == 0 and e == 0)]
        total += len(choices) * 5 ** (L - i - 1)
        if d % 2:
            return total
    return total + 1


def even_digits(s):
    L, R = ints(s)
    return str(_even_upto(R) - _even_upto(L - 1))


def restaurant(s):
    v = ints(s)
    n, m = v[:2]
    W = [(v[2 + 3 * i], v[3 + 3 * i], v[4 + 3 * i]) for i in range(n)]
    a = v[2 + 3 * n:2 + 3 * n + m]
    diff = [0] * (m + 2)
    for l, r, x in W:
        diff[l] += x; diff[r + 1] -= x
    D, cur = [0] * (m + 1), 0
    for i in range(1, m + 1):
        cur += diff[i]
        D[i] = cur - a[i - 1]
    p1 = [0] * (m + 1); p2 = [0] * (m + 1)
    for i in range(1, m + 1):
        p1[i] = p1[i - 1] + D[i]; p2[i] = p2[i - 1] + D[i] * D[i]
    tot1 = p1[m]
    res = []
    for idx, (l, r, x) in enumerate(W, 1):
        ln = r - l + 1
        s1 = p1[r] - p1[l - 1]; s2 = p2[r] - p2[l - 1]
        if s1 == tot1 and s1 == x * ln and s2 == x * x * ln and p2[m] == s2:
            res.append(idx)
    return " ".join(map(str, res))
