include $(TOPDIR)/rules.mk
include $(TOPDIR)/vlmcsd-build.mk
LUCI_TITLE:=LuCI support for vlmcsd
LUCI_DEPENDS:=+luci-base +vlmcsd +vlmcs +rpcd-mod-ucode +ucode-mod-fs +ucode-mod-ubus +ucode-mod-uci
LUCI_PKGARCH:=all
PKG_PO_VERSION:=$(PACKAGE_VERSION)
PKG_SRC_VERSION:=$(UPSTREAM_VERSION)
# Pin the complete package version: older/newer LuCI differ in revision suffixing.
PKG_VERSION:=$(PACKAGE_VERSION)
PKG_RELEASE:=
PKG_LICENSE:=Apache-2.0
PKG_FILE_DEPENDS += $(TOPDIR)/vlmcsd-build.mk /work/scripts/inside-build.sh
include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
