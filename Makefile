DESTDIR ?= /
PREFIX ?= /usr
SYSCONFDIR ?= /etc

USR = $(DESTDIR)/$(PREFIX)
BIN = $(USR)/bin/
ETC = $(DESTDIR)/etc/
SHARE = $(USR)/share/noodlehub
SYSTEMD_UNITS = $(ETC)/systemd/system/

package:
	sed -i -r -e "s@(^SYSCONFDIR = ').*('$$)@\1$(ETC)/noodle_hub/\2@" noodle_hub
	sed -i -r -e "s@(^TEMPLATEDIR = ').*('$$)@\1$(SHARE)\2@" noodle_hub 

install:
	mkdir -p $(BIN)
	mkdir -p $(SHARE)
	mkdir -p $(SYSTEMD_UNITS)
	mkdir -p $(ETC)
	mkdir -p $(ETC)/noodle_hub

	install noodle_hub $(BIN)
	install -m 0644 config.yaml $(ETC)/noodle_hub
	install -m 0644 systemd/noodle_hub.service $(SYSTEMD_UNITS)

	cp -av templates $(SHARE)
