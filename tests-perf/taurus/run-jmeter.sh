#!/bin/sh
jmeter -n -t './TestSendNotification.jmx' -j test.log -q config.properties