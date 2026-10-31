cask "icemelt" do
  version "2026.4.1"
  sha256 "88cfdb2c32731d288c4cc0c70f35b12f864e8cf75184ee128d4518c87882ecd3"

  url "https://github.com/pnikolaidis/icemelt/releases/download/v#{version}/IceMelt-#{version}.dmg"
  name "IceMelt"
  desc "Menu bar manager, maintained fork of Ice"
  homepage "https://github.com/pnikolaidis/icemelt"

  livecheck do
    url "https://pnikolaidis.github.io/icemelt/appcast.xml"
    strategy :sparkle, &:short_version
  end

  auto_updates true
  depends_on macos: :tahoe

  app "IceMelt.app"

  uninstall quit:       "com.pnikolaidis.icemelt",
            login_item: "IceMelt"

  zap trash: [
    "~/Library/Caches/com.pnikolaidis.icemelt",
    "~/Library/HTTPStorages/com.pnikolaidis.icemelt",
    "~/Library/Preferences/com.pnikolaidis.icemelt.plist",
    "~/Library/WebKit/com.pnikolaidis.icemelt",
  ]
end
