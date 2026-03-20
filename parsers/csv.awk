# csv.awk - Generic library for associative-array -> CSV output.
#
# Usage in a parser:
#   1. Call csv_col(name, default, fmt) once per column in BEGIN, in order.
#      "Failed" is reserved and auto-computed — do not register it.
#   2. Set _csv_failed_key to the column whose empty value signals failure.
#   3. Call csv_init() after all csv_col() declarations to print the header.
#   4. In pattern rules, set data[key] = value as needed.
#   5. Call csv_flush() to emit a row (resets data automatically).
#
# parse_marker() and strip_prefix() are shared helpers for the standard
# __BEGIN_FILE__ marker format:
#   <graph>__k<K>__s<Seed>__e<Epsilon>__<nodes>x<mpis>x<threads>.log

function csv_col(name, default, fmt) {
    _csv_ncols++
    _csv_cols[_csv_ncols] = name
    _csv_defaults[name]   = default
    _csv_fmts[name]       = fmt
}

function csv_init() {
    _csv_print_header()
    csv_reset()
}

function csv_reset(    i) {
    delete data
    for (i = 1; i <= _csv_ncols; i++) {
        data[_csv_cols[i]] = _csv_defaults[_csv_cols[i]]
    }
}

function csv_flush(    i, sep, col, failed) {
    if (data["Graph"] == "") return

    failed = (_csv_failed_key != "" && data[_csv_failed_key] == "") ? 1 : 0

    sep = ""
    for (i = 1; i <= _csv_ncols; i++) {
        col = _csv_cols[i]
        printf sep _csv_fmts[col], data[col]
        sep = ","
    }
    printf ",%d\n", failed

    csv_reset()
}

function _csv_print_header(    i, sep) {
    sep = ""
    for (i = 1; i <= _csv_ncols; i++) {
        printf "%s%s", sep, _csv_cols[i]
        sep = ","
    }
    print ",Failed"
}

# ---------------------------------------------------------------------------
# Shared marker / utility helpers
# ---------------------------------------------------------------------------

# Parses a log filename (without path) of the form:
#   <graph>__k<K>__s<Seed>__e<Epsilon>__<nodes>x<mpis>x<threads>[.log]
# and populates data["Graph"], data["K"], data["Seed"], data["Epsilon"],
# data["NumNodes"], data["NumMPIsPerNode"], data["NumThreadsPerMPI"].
function parse_marker(marker,    parts, n, i, graph) {
    sub(/\.log$/, "", marker)

    n = split(marker, parts, "__")
    if (n < 5) {
        data["Graph"] = marker
        return
    }

    graph = parts[1]
    for (i = 2; i <= n - 4; ++i) {
        graph = graph "__" parts[i]
    }
    data["Graph"] = graph

    data["K"]       = strip_prefix(parts[n - 3], "k") + 0
    data["Seed"]    = strip_prefix(parts[n - 2], "s") + 0
    data["Epsilon"] = strip_prefix(parts[n - 1], "e") + 0

    split(parts[n], topo, "x")
    if (length(topo[1]) > 0) data["NumNodes"]         = topo[1] + 0
    if (length(topo[2]) > 0) data["NumMPIsPerNode"]   = topo[2] + 0
    if (length(topo[3]) > 0) data["NumThreadsPerMPI"] = topo[3] + 0
}

function strip_prefix(text, prefix,    value) {
    value = text
    sub("^" prefix, "", value)
    return value
}
