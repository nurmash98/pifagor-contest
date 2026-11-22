# Общие пути и загрузка настроек (подключается через source)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
PIFAGOR_DATA="$HOME/pifagor-data"
PIFAGOR_ENV="$PIFAGOR_DATA/pifagor.env"
load_env() {   # KEY=значение построчно; значение берётся как есть (без выполнения)
  local f="$1" line k v
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$k=$v"
  done < "$f"
}
