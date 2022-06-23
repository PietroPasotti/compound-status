#!/usr/bin/env bash
LIB_V=${LIB_VERSION:-v0}
charmcraft publish-lib "charms.compound_status.$LIB_V.compound_status"  # $ TEMPLATE: Filled in by ./scripts/init.sh
