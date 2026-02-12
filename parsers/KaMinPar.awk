BEGIN {
    print_header()
    reset_data()
    state = "SUMMARY"
}

/^__BEGIN_FILE__/ {
    flush_record()
    reset_data()

    marker = $0
    sub(/^__BEGIN_FILE__[[:space:]]+/, "", marker)
    parse_marker(marker)
    state = "SUMMARY"
    next
}

/^__END_FILE__/ {
    flush_record()
    next
}

$0 == "############################################################### Partitioning ###" {
    state = "PARTITIONING"
}

/Partitioning:/ {
    value = $0
    sub(/^.*Partitioning:[[:space:].]*/, "", value)
    sub(/[[:space:]]*s.*$/, "", value)
    if (value != "") {
        data["Time"] = value + 0
    }
}

/Imbalance:/ {
    value = $0
    sub(/^.*Imbalance:[[:space:]]*/, "", value)
    sub(/[[:space:]].*$/, "", value)
    if (value != "") {
        data["Imbalance"] = value + 0
    }
}

/Edge cut:/ {
    value = $0
    sub(/^.*Edge cut:[[:space:]]*/, "", value)
    sub(/[[:space:]].*$/, "", value)
    if (value != "") {
        data["Cut"] = value + 0
    }
}

/Number of nodes:/ {
    if (state == "SUMMARY") {
        value = $0
        sub(/^.*Number of nodes:[[:space:]]*/, "", value)
        sub(/[[:space:]].*$/, "", value)
        if (value != "") {
            data["N"] = value + 0
        }
    }
}

/Number of edges:/ {
    if (state == "SUMMARY") {
        value = $0
        sub(/^.*Number of edges:[[:space:]]*/, "", value)
        sub(/[[:space:]].*$/, "", value)
        if (value != "") {
            data["M"] = value + 0
        }
    }
}

END {
    flush_record()
}

function print_header() {
    print "Graph,N,M,K,Seed,Cut,Epsilon,Imbalance,Time,NumNodes,NumMPIsPerNode,NumThreadsPerMPI,Failed"
}

function reset_data() {
    delete data
    data["Graph"] = ""
    data["N"] = ""
    data["M"] = ""
    data["K"] = ""
    data["Seed"] = ""
    data["Cut"] = ""
    data["Epsilon"] = 0.03
    data["Imbalance"] = ""
    data["Time"] = ""
    data["NumNodes"] = -1
    data["NumMPIsPerNode"] = -1
    data["NumThreadsPerMPI"] = -1
}

function parse_marker(marker, parts, n, i, graph) {
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

    data["K"] = strip_prefix(parts[n - 3], "k") + 0
    data["Seed"] = strip_prefix(parts[n - 2], "s") + 0
    data["Epsilon"] = strip_prefix(parts[n - 1], "e") + 0

    split(parts[n], topo, "x")
    if (length(topo[1]) > 0) data["NumNodes"] = topo[1] + 0
    if (length(topo[2]) > 0) data["NumMPIsPerNode"] = topo[2] + 0
    if (length(topo[3]) > 0) data["NumThreadsPerMPI"] = topo[3] + 0
}

function strip_prefix(text, prefix) {
    value = text
    sub("^" prefix, "", value)
    return value
}

function value_or_zero(key) {
    return (data[key] == "" ? 0 : data[key])
}

function flush_record(failed) {
    if (data["Graph"] == "") {
        return
    }

    failed = (data["Time"] == "") ? 1 : 0

    printf "%s,%d,%d,%d,%d,%d,%g,%g,%g,%d,%d,%d,%d\n", \
      data["Graph"], \
      value_or_zero("N"), \
      value_or_zero("M"), \
      value_or_zero("K"), \
      value_or_zero("Seed"), \
      value_or_zero("Cut"), \
      value_or_zero("Epsilon"), \
      value_or_zero("Imbalance"), \
      value_or_zero("Time"), \
      value_or_zero("NumNodes"), \
      value_or_zero("NumMPIsPerNode"), \
      value_or_zero("NumThreadsPerMPI"), \
      failed

    reset_data()
}
