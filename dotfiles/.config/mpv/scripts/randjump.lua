math.randomseed(os.time())

function randomJump()
    duration = mp.get_property_number("duration")
    target = math.floor(duration * math.random())
    mp.set_property("time-pos", target)
end

mp.add_key_binding("ctrl+R", randomJump)
