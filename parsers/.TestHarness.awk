BEGIN {
  print "file,raw"
}

/^__BEGIN_FILE__/ {
  current_file = $2
  next
}

/^test-harness:/ {
  raw = substr($0, index($0, $2))
  gsub(/"/, "\"\"", raw)
  print current_file ",\"" raw "\""
}
