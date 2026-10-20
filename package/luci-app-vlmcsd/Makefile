include $(TOPDIR)/rules.mk
include $(TOPDIR)/vlmcsd-build.mk
LUCI_TITLE:=LuCI support for vlmcsd
LUCI_DEPENDS:=+luci-base +vlmcsd +vlmcs +rpcd-mod-ucode +ucode-mod-fs +ucode-mod-ubus +ucode-mod-uci
LUCI_PKGARCH:=all
PKG_PO_VERSION:=$(PACKAGE_VERSION)
PKG_SRC_VERSION:=$(UPSTREAM_VERSION)
PKG_VERSION:=$(UPSTREAM_VERSION)
PKG_RELEASE:=$(PACKAGE_RELEASE)
PKG_LICENSE:=Apache-2.0
include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
