# Подготовка к районным олимпиадам — исходники тестов

- `sol.py` — эталонные решения 31 задачи;
- `brute.py` — сверка эталонов с переборными решениями на маленьких тестах (`python3 brute.py` → `fails 0`);
- `data.py` — условия и генераторы тестов (по 10 тестов, первые — примеры из условия);
- `build.py` — сборка `contest/data/district_olympiads.json.gz`:
  `python3 build.py ../../contest/data/district_olympiads.json.gz`

На сайт загружается миграцией `0077_district_olympiads` (функция `contest.district_olympiads.load`).
