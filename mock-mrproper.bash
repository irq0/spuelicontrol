#!/bin/bash

#
# mock implementation of spueli's control panel serial protocol
# server side
#

set -o errexit

SCRIPTNAME=$(basename $0)
SCRIPTPATH=$(dirname $0)


#
# Boilerplate..
#

declare -A color
color["red"]='\e[00;31m' # Red
color["green"]='\e[00;32m' # Green
color["yellow"]='\e[00;33m' # Yellow
color["blue"]='\e[00;34m' # Blue
color["purple"]='\e[00;35m' # Purple
color["cyan"]='\e[00;36m' # Cyan
color["white"]='\e[00;37m' # White

function echo-with-color-code
{
    reset='\e[00m'
    echo -e "${1}${*:2}${reset}"
    return 0
}

function echo-with-color
{
    echo-with-color-code ${color[$1]} "${*:2}"
    return 0
}

function log()
{
    echo-with-color green "${SCRIPTNAME}: ${*}" >&2
    return 0
}

function err()
{
    echo-with-color red "${SCRIPTNAME}: ${*}" >&2
    return 0
}

function log_request()
{
    echo-with-color green "<--- ${*}" >&2
    return 0

}

function log_reply()
{
    echo-with-color yellow "---> ${*}" >&2
    return 0
}

function log_event()
{
    echo-with-color purple "---> ${*}" >&2
    return 0
}


function reply()
{
    log_reply $@
    echo $@
}

function request()
{
    log_request $@
    echo $@
}

function event()
{
    log_event $@
    echo $@
}


#
# State
#

declare -A LEDS
declare -A SWITCHES

VERSION="2342"

SWITCHES["0"]="ON"
SWITCHES["1"]="ON"
SWITCHES["2"]="OFF"
SWITCHES["3"]="ON"
SWITCHES["4"]="ON"
SWITCHES["5"]="OFF"
SWITCHES["6"]="ON"
SWITCHES["7"]="ON"
SWITCHES["127"]="ON"

LEDS["0"]="#ABCDEF"
LEDS["1"]="#ABCDEF"
LEDS["2"]="#ABCDEF"
LEDS["3"]="#ABCDEF"
LEDS["4"]="#ABCDEF"
LEDS["5"]="#ABCDEF"
LEDS["6"]="#000000"
LEDS["7"]="#FFFFFF"

export SWITCHES LEDS

function recv_loop()
{
    while read -r -a args; do
	log_request "$args"
	case "${args[0]}" in
            "LED")
		LEDS[${args[1]}]="${args[2]}"
		;;
	    "RESET")
		err "Not implemented. Restart the damn script.."
		;;
	    "GET")
		case "${args[1]}" in
		    "SWITCHES")
			reply SWITCHES "$(
			    for sw in "${!SWITCHES[@]}"; do
				echo -n "$sw:${SWITCHES[$sw]} "
			    done)"
			;;
		    "LEDS")
			reply LEDS "$(
			    for sw in "${!LEDS[@]}"; do
				echo -n "$sw:${LEDS[$sw]} "
			    done)"
			;;
		    "LED")
			reply LED ${args[2]} ${LEDS[${args[2]}]}
			;;
		    *)
			err "Foo"
			;;
		esac
		;;
	    "PING")
		reply PONG $(date +%s)
		;;
	    "VERSION")
		reply VERSION $VERSION
		;;
	    "%SWITCH")
		SWITCHES["${args[1]}"]="${args[2]}"
		event SWITCH "${args[1]}" "${args[2]}"
		;;
            *)
		err "Unknown command: $args"
	esac
    done
}

function main()
{
    event STARTUP mrproper 2342
    recv_loop
}

main
exit 1
