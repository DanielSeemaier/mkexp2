#!/usr/bin/env zsh

PurgeExperimentDirectory() {
  if [[ ! -f "$PWD/Experiment" ]]; then
    EchoFatal "no Experiment file in current directory"
    return 1
  fi

  local -a paths=()
  paths=("$PWD"/*(N) "$PWD"/.[!.]*(N) "$PWD"/..?*(N))

  local entry=""
  local name=""
  local removed=0
  for entry in "${paths[@]}"; do
    name="${entry:t}"
    [[ "$name" == "Experiment" ]] && continue
    rm -rf -- "$entry"
    EchoInfo "removed $name"
    removed=$((removed + 1))
  done

  EchoStep "Purged experiment directory; kept Experiment"
  if (( removed == 0 )); then
    EchoInfo "nothing to remove"
  fi
}
