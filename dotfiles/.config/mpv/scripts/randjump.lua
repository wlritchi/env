math.randomseed(os.time())

function randomJump()
    duration = mp.get_property_number("duration")
    target = duration * math.random()
    mp.set_property("time-pos", target)
end

mp.add_key_binding("ctrl+R", randomJump)
