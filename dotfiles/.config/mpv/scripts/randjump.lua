enabled = false
counter = 30

math.randomseed(os.time())

function randomJump()
    counter = 30
    duration = mp.get_property_number("duration")
    target = duration * math.random()
    mp.set_property("time-pos", target)
end

function doTick(val)
    if (enabled) then
        counter = counter - 1
        if (counter < 0) then
            randomJump()
        end
    end
end

function toggleEnabled()
    if (enabled) then
        enabled = false
        mp.osd_message("random segments disabled")
    else
        enabled = true
        randomJump()
        mp.osd_message("random segments enabled")
    end
end

mp.add_periodic_timer(1.0, doTick)
mp.add_key_binding("ctrl+R", randomJump)
mp.add_key_binding("ctrl+L", toggleEnabled)
