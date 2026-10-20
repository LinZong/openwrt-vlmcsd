include $(TOPDIR)/rules.mk
include $(TOPDIR)/vlmcsd-build.mk

PKG_NAME:=vlmcsd
PKG_VERSION:=$(UPSTREAM_VERSION)
PKG_RELEASE:=$(PACKAGE_RELEASE)
PKG_SOURCE_VERSION:=$(UPSTREAM_COMMIT)
PKG_SOURCE:=$(PKG_NAME)-$(PKG_VERSION).tar.gz
PKG_SOURCE_URL:=$(UPSTREAM_ARCHIVE_URL)?
PKG_HASH:=$(UPSTREAM_SHA256)
PKG_BUILD_DIR:=$(BUILD_DIR)/vlmcsd-$(PKG_SOURCE_VERSION)
PKG_LICENSE:=LicenseRef-TheFlightSims-OSS AND Apache-2.0
PKG_LICENSE_FILES:=LICENSE
PKG_BUILD_PARALLEL:=1
PKG_ASLR_PIE_REGULAR:=1

include $(INCLUDE_DIR)/package.mk

define Package/vlmcsd
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Volume License Management Service
  URL:=$(UPSTREAM_URL)
  DEPENDS:=+libpthread +libubox +uci +jsonfilter
  USERID:=vlmcsd:vlmcsd
endef

define Package/vlmcs
  SECTION:=net
  CATEGORY:=Network
  TITLE:=vlmcsd protocol diagnostic client
  URL:=$(UPSTREAM_URL)
endef

define Package/vlmcsd/conffiles
/etc/config/vlmcsd
endef

MAKE_FLAGS += CC="$(TARGET_CC)" AR="$(TARGET_AR)" \
  CFLAGS="$(TARGET_CFLAGS) $(TARGET_CPPFLAGS)" LDFLAGS="$(TARGET_LDFLAGS)" \
  SAFE_MODE=1 THREADS=1 FEATURES=full CRYPTO=internal DNS_PARSER=internal NOLRESOLV=1 \
  STRIP=0 MAX_THREADS=1 SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) \
  VLMCSD_VERSION="$(VLMCSD_BUILD_VERSION)"

define Build/Compile
	$(call Build/Compile/Default,vlmcsd vlmcs)
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) ./src/vlmcsd-timeout.c $(TARGET_LDFLAGS) -o $(PKG_BUILD_DIR)/bin/vlmcsd-timeout
endef

define Package/vlmcsd/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/config $(1)/etc/init.d $(1)/usr/libexec $(1)/usr/share/vlmcsd
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/bin/vlmcsd $(1)/usr/sbin/vlmcsd
	$(INSTALL_BIN) ./files/vlmcsd.init $(1)/etc/init.d/vlmcsd
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/bin/vlmcsd-timeout $(1)/usr/libexec/vlmcsd-timeout
	$(INSTALL_BIN) ./files/vlmcsd-helper $(1)/usr/libexec/vlmcsd-helper
	$(INSTALL_CONF) ./files/vlmcsd.config $(1)/etc/config/vlmcsd
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/LICENSE $(1)/usr/share/vlmcsd/LICENSE
	$(INSTALL_DATA) ./files/LICENSE.integration $(1)/usr/share/vlmcsd/LICENSE.integration
	printf '%s\n' '$(PKG_VERSION)-r$(PKG_RELEASE)' > $(1)/usr/share/vlmcsd/version
	printf '%s\n' '$(PKG_SOURCE_VERSION)' > $(1)/usr/share/vlmcsd/source-commit
endef

define Package/vlmcs/install
	$(INSTALL_DIR) $(1)/usr/bin $(1)/usr/share/licenses/vlmcs
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/LICENSE $(1)/usr/share/licenses/vlmcs/LICENSE
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/bin/vlmcs $(1)/usr/bin/vlmcs
endef

$(eval $(call BuildPackage,vlmcsd))
$(eval $(call BuildPackage,vlmcs))
