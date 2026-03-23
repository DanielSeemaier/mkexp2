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
    state = "SUMMARY"
}

/^__BEGIN_FILE__/ {
    csv_flush()
    marker = $0
    sub(/^__BEGIN_FILE__[[:space:]]+/, "", marker)
    parse_marker(marker)
    state = "SUMMARY"
    next
}

/^__END_FILE__/ {
    csv_flush()
    next
}

END {
    csv_flush()
}

$0 == "############################################################### Partitioning ###" {
    state = "PARTITIONING"
}

/Partitioning:/ {
    value = $0
    sub(/^.*Partitioning:[[:space:].]*/, "", value)
    sub(/[[:space:]]*s.*$/, "", value)
    if (value != "") data["Time"] = value + 0
}

/Imbalance:/ {
    value = $0
    sub(/^.*Imbalance:[[:space:]]*/, "", value)
    sub(/[[:space:]].*$/, "", value)
    if (value != "") data["Imbalance"] = value + 0
}

/Edge cut:/ {
    value = $0
    sub(/^.*Edge cut:[[:space:]]*/, "", value)
    sub(/[[:space:]].*$/, "", value)
    if (value != "") data["Cut"] = value + 0
}

/Number of nodes:/ {
    if (state == "SUMMARY") {
        value = $0
        sub(/^.*Number of nodes:[[:space:]]*/, "", value)
        sub(/[[:space:]].*$/, "", value)
        if (value != "") data["N"] = value + 0
    }
}

/Number of edges:/ {
    if (state == "SUMMARY") {
        value = $0
        sub(/^.*Number of edges:[[:space:]]*/, "", value)
        sub(/[[:space:]].*$/, "", value)
        if (value != "") data["M"] = value + 0
    }
}
