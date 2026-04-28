BEGIN {
    csv_col("Graph",            "",    "%s")
    csv_col("N",                0,     "%d")
    csv_col("M",                0,     "%d")
    csv_col("K",                0,     "%d")
    csv_col("Seed",             0,     "%d")
    csv_col("Cut",              0,     "%d")
    csv_col("Epsilon",          0.03,  "%g")
    csv_col("Imbalance",        0,     "%g")
    csv_col("Time",             "",    "%g")
    csv_col("NumNodes",         -1,    "%d")
    csv_col("NumMPIsPerNode",   -1,    "%d")
    csv_col("NumThreadsPerMPI", -1,    "%d")

    _csv_failed_key = "Time"
    csv_init()
    reset_mt_kahypar_state()
}

function reset_mt_kahypar_state() {
    seen_input_stats = 0
}

/^__BEGIN_FILE__/ {
    csv_flush()
    marker = $0
    sub(/^__BEGIN_FILE__[[:space:]]+/, "", marker)
    parse_marker(marker)
    reset_mt_kahypar_state()
    next
}

/^__END_FILE__/ {
    csv_flush()
    reset_mt_kahypar_state()
    next
}

END {
    csv_flush()
}

/# HNs:[[:space:]]*/ {
    if (!seen_input_stats) {
        value = $0
        sub(/^.*# HNs:[[:space:]]*/, "", value)
        sub(/[[:space:]].*$/, "", value)
        if (value != "") data["N"] = value + 0

        value = $0
        sub(/^.*# HEs:[[:space:]]*/, "", value)
        sub(/[[:space:]].*$/, "", value)
        if (value != "") data["M"] = value + 0

        seen_input_stats = 1
    }
}

/^[[:space:]]*(cut|km1|soed|steiner_tree)[[:space:]]*=/ {
    value = $0
    sub(/^[[:space:]]*(cut|km1|soed|steiner_tree)[[:space:]]*=[[:space:]]*/, "", value)
    sub(/[[:space:]].*$/, "", value)
    if (value != "") data["Cut"] = value + 0
}

/^[[:space:]]*[Ii]mbalance[[:space:]]*=/ {
    value = $0
    sub(/^[[:space:]]*[Ii]mbalance[[:space:]]*=[[:space:]]*/, "", value)
    sub(/[[:space:]].*$/, "", value)
    if (value != "") data["Imbalance"] = value + 0
}

/^[[:space:]]*Partitioning Time[[:space:]]*=/ {
    value = $0
    sub(/^[[:space:]]*Partitioning Time[[:space:]]*=[[:space:]]*/, "", value)
    sub(/[[:space:]]*s.*$/, "", value)
    if (value != "") data["Time"] = value + 0
}
