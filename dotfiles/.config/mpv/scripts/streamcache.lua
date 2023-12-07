-- configuration

initSpeed = 1
minSpeed = 0.95
maxSpeed = 1.5
minAdjPerSecond = -0.05
maxAdjPerSecond = 0.001
targetBufferSeconds = 5
targetAcceptableRange = 2 -- +- n seconds -> play at realtime
autoAdjustTargetBuffer = true -- not implemented yet
enabled = true

-- implementation

utils = require "mp.utils"

currentSpeed = initSpeed

function log(msg)
    mp.msg.info(msg)
end

function setSpeed(val)
    currentSpeed = val
    mp.set_property("speed", val)
    log("set speed to " .. val)
end

function onLoad()
    if (not mp.get_property_bool("demuxer-via-network")) then
        log("new file, but not demuxed via network. disabling")
        setSpeed(1)
        return
    end
    log("new file, setting to initial speed")
    setSpeed(initSpeed)
end

function adjustSpeed()
    if (not enabled or not mp.get_property_bool("demuxer-via-network")) then
        return
    end
    cacheDuration = mp.get_property_number("demuxer-cache-duration")
    if (cacheDuration == nil) then
        log("no cache duration provided")
        return
    end
    if (cacheDuration < targetBufferSeconds - targetAcceptableRange) then
        if (currentSpeed + minAdjPerSecond <= minSpeed) then
            setSpeed(minSpeed)
        else
            setSpeed(currentSpeed + minAdjPerSecond)
        end
    elseif (cacheDuration > targetBufferSeconds + targetAcceptableRange) then
        if (currentSpeed + maxAdjPerSecond >= maxSpeed) then
            setSpeed(maxSpeed)
        else
            setSpeed(currentSpeed + maxAdjPerSecond)
        end
    elseif (currentSpeed > 1) then
        if (currentSpeed + minAdjPerSecond <= 1) then
            setSpeed(1)
        else
            setSpeed(currentSpeed + minAdjPerSecond)
        end
    elseif (currentSpeed < 1) then
        if (currentSpeed + maxAdjPerSecond >= 1) then
            setSpeed(1)
        else
            setSpeed(currentSpeed + maxAdjPerSecond)
        end
    end
end

function toggleEnabled()
    if (enabled) then
        enabled = false
        setSpeed(1)
        mp.osd_message("streamcache disabled")
    else
        enabled = true
        mp.osd_message("streamcache enabled")
    end
end

mp.register_event("file-loaded", onLoad)
mp.add_periodic_timer(1.0, adjustSpeed)
mp.add_key_binding("ctrl+S", toggleEnabled)
