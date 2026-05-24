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
}

/^__BEGIN_FILE__/ {
    csv_flush()
    marker = $0
    sub(/^__BEGIN_FILE__[[:space:]]+/, "", marker)
    parse_marker(marker)
    next
}

/^__END_FILE__/ {
    csv_flush()
    next
}

END {
    csv_flush()
}

/^n:[0-9]+[[:space:]]+m:[[:space:]]*[0-9]+/ {
    line = $0
    sub(/^n:/, "", line)
    split(line, parts, /[[:space:]]+m:[[:space:]]*/)
    data["N"] = parts[1] + 0
    data["M"] = parts[2] + 0
}

/^log>total partitioning time elapsed[[:space:]]+/ {
    data["Time"] = $5 + 0
}

/^log>final edge cut[[:space:]]+/ {
    data["Cut"] = $4 + 0
}

/^log>final balance[[:space:]]+[0-9.]/ {
    value = $3 + 0
    data["Imbalance"] = value >= 1 ? value - 1 : value
}
