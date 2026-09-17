-- A connection-scoped ledger precedes every possible EIP effect. Terminal
-- publication, retained result and ACK share one scripting domain. Never ACK
-- first: a runtime failure can leave a response without an ACK, not the reverse.
local requests, ledger, expiries, responses = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local input = cjson.decode(ARGV[1])
local limits = input.limits
local function reply(code, fields)
    fields = fields or {}
    fields.code = code
    return cjson.encode(fields)
end
local function kind(key) return redis.call('TYPE', key).ok end
local function group_exists(key, name)
    if kind(key) ~= 'stream' then return false end
    for _, group in ipairs(redis.call('XINFO', 'GROUPS', key)) do
        for index = 1, #group, 2 do
            if group[index] == 'name' and group[index + 1] == name then return true end
        end
    end
    return false
end
local function touch(key) redis.call('PEXPIRE', key, limits.scope_retention_ms) end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local server = input.memory_server_id
if server == '' then server = string.match(redis.call('INFO', 'server'), 'run_id:([^\r\n]+)') end
if not server then return reply('relay_unavailable') end

if input.operation == 'prepare_worker' then
    if kind(responses) ~= 'none' then return reply('scope_exists') end
    redis.call('XGROUP', 'CREATE', responses, 'worker', '0', 'MKSTREAM')
    touch(responses)
    return reply('ok')
elseif input.operation == 'touch_worker' then
    if not group_exists(responses, 'worker') then return reply('scope_lost') end
    touch(responses)
    return reply('ok')
elseif input.operation == 'ack_responses' then
    if not group_exists(responses, 'worker') then return reply('scope_lost') end
    if #input.entry_ids < 1 or #input.entry_ids > 128 then return reply('request_invalid') end
    for _, id in ipairs(input.entry_ids) do
        local pending = redis.call('XPENDING', responses, 'worker', id, id, 1)
        if #pending ~= 0 and pending[1][2] ~= input.worker then return reply('request_not_owned') end
    end
    redis.call('XACK', responses, 'worker', unpack(input.entry_ids))
    redis.call('XDEL', responses, unpack(input.entry_ids))
    return reply('ok')
elseif input.operation == 'prepare_connection' then
    if kind(requests) ~= 'none' or kind(ledger) ~= 'none' or kind(expiries) ~= 'none' then
        return reply('scope_exists')
    end
    redis.call('XGROUP', 'CREATE', requests, 'owner', '0', 'MKSTREAM')
    touch(requests)
    redis.call('HSET', ledger, '_scope', input.scope, '_server', server, '_count', 0, '_bytes', 0)
    touch(ledger)
    redis.call('ZADD', expiries, '+inf', '_scope')
    touch(expiries)
    return reply('ok')
end

if kind(ledger) ~= 'hash' or kind(expiries) ~= 'zset' or not group_exists(requests, 'owner') then
    return reply('scope_lost')
end
if redis.call('HGET', ledger, '_scope') ~= input.scope or redis.call('HGET', ledger, '_server') ~= server then
    return reply('scope_lost')
end
local count, bytes = tonumber(redis.call('HGET', ledger, '_count')), tonumber(redis.call('HGET', ledger, '_bytes'))
if not count or not bytes or count < 0 or bytes < 0 then return reply('scope_lost') end
if redis.call('HLEN', ledger) ~= count + 4 then return reply('scope_lost') end

if input.operation == 'touch_connection' then
    touch(requests); touch(ledger); touch(expiries)
    return reply('ok')
elseif input.operation == 'prune' then
    local ids = redis.call('ZRANGEBYSCORE', expiries, '-inf', now, 'LIMIT', 0, 32)
    -- Validate the whole bounded batch before cleanup mutations.
    local records = {}
    for _, id in ipairs(ids) do
        local raw = redis.call('HGET', ledger, id)
        if not raw then return reply('scope_lost') end
        local record = cjson.decode(raw)
        if not record.cost or not record.entry_id then return reply('scope_lost') end
        records[#records + 1] = record
    end
    for index, id in ipairs(ids) do
        local record = records[index]
        if record.entry_id ~= '' then
            redis.call('XACK', requests, 'owner', record.entry_id)
            redis.call('XDEL', requests, record.entry_id)
        end
        redis.call('HDEL', ledger, id)
        redis.call('ZREM', expiries, id)
        count, bytes = count - 1, bytes - record.cost
    end
    redis.call('HSET', ledger, '_count', count, '_bytes', bytes)
    return reply('ok')
end

local request_json, id = input.request_json, input.request_id
if not request_json or #request_json > limits.request_bytes or not id then return reply('request_invalid') end
local raw = redis.call('HGET', ledger, id)
local record = raw and cjson.decode(raw) or nil
if record and not redis.call('ZSCORE', expiries, id) then return reply('scope_lost') end
if record and (record.request_json ~= request_json or record.response_key ~= responses) then
    return reply('request_conflict')
end
if not group_exists(responses, 'worker') then return reply('response_scope_lost') end
local byte_limit = limits.pending_bytes
local count_limit = limits.retained_requests
if not input.is_control then
    byte_limit = byte_limit - limits.control_requests * (limits.control_bytes * 4 + 2048)
    count_limit = count_limit - limits.control_requests
end

if input.operation == 'append' then
    if input.deadline_ms <= now or input.deadline_ms > now + limits.delivery_ms then return reply('request_expired') end
    if record then
        if record.phase == 'reserved' then return reply('outcome_unknown') end
        return reply('ok', {phase = record.phase, entry_id = record.entry_id, response_json = record.response_json})
    end
    -- Conservative retained-memory accounting includes JSON string escaping.
    local cost = #request_json * 2 + 1024
    if count >= count_limit or bytes + cost > byte_limit then return reply('relay_overloaded') end
    record = {request_json = request_json, response_key = responses, phase = 'reserved', entry_id = '', cost = cost}
    redis.call('HSET', ledger, id, cjson.encode(record), '_count', count + 1, '_bytes', bytes + cost)
    redis.call('ZADD', expiries, input.deadline_ms + limits.evidence_ms, id)
    local entry = redis.call('XADD', requests, '*', 'request_id', id, 'request', request_json)
    record.entry_id, record.phase = entry, 'queued'
    redis.call('HSET', ledger, id, cjson.encode(record))
    return reply('ok', {phase = 'queued', entry_id = entry})
end

if not record then return reply('outcome_unknown') end
if record.phase == 'reserved' then return reply('outcome_unknown') end
if not input.entry_id then return reply('request_invalid') end

-- Completed originals may already have been ACKed/deleted. Other entries must
-- still belong to this owner and carry the exact canonical request.
local completed_original = record.phase == 'completed' and input.entry_id == record.entry_id
if not completed_original then
    local pending = redis.call('XPENDING', requests, 'owner', input.entry_id, input.entry_id, 1)
    local entries = redis.call('XRANGE', requests, input.entry_id, input.entry_id)
    if #pending ~= 1 or pending[1][2] ~= input.owner or #entries ~= 1 then return reply('request_not_owned') end
    local fields, found_id, found_request = entries[1][2], nil, nil
    for index = 1, #fields, 2 do
        if fields[index] == 'request_id' then found_id = fields[index + 1] end
        if fields[index] == 'request' then found_request = fields[index + 1] end
    end
    if found_id ~= id or found_request ~= request_json then return reply('request_conflict') end
end

if input.operation == 'start' then
    if input.deadline_ms <= now then return reply('request_expired') end
    if record.phase ~= 'queued' then
        return reply('ok', {phase = record.phase, entry_id = record.entry_id, response_json = record.response_json})
    end
    if record.entry_id ~= input.entry_id then return reply('request_not_owned') end
    record.phase = 'inflight'
    redis.call('HSET', ledger, id, cjson.encode(record))
    return reply('ok', {phase = 'started', entry_id = record.entry_id})
end

local response_json = input.response_json
if not response_json or #response_json > limits.response_bytes then return reply('response_invalid') end
if input.operation == 'chunk' then
    if input.deadline_ms <= now then return reply('request_expired') end
    if record.phase ~= 'inflight' then return reply('request_not_started') end
    local position = input.transfer
    local stream = record.stream or {transfer_id = position.transfer_id, sequence = 0, offset = 0}
    if stream.transfer_id ~= position.transfer_id or stream.sequence ~= position.sequence or stream.offset ~= position.offset or
       input.chunk_bytes > limits.chunk_bytes then return reply('transfer_conflict') end
    if redis.call('XLEN', responses) >= limits.response_frames - limits.terminal_reserve then
        return reply('relay_overloaded')
    end
    redis.call('XADD', responses, '*', 'frame', response_json)
    record.stream = {transfer_id = stream.transfer_id, sequence = stream.sequence + 1, offset = stream.offset + input.chunk_bytes}
    redis.call('HSET', ledger, id, cjson.encode(record))
    return reply('ok')
elseif input.operation ~= 'complete' then
    return reply('operation_invalid')
end

if record.phase == 'completed' then
    if record.response_json ~= response_json then return reply('result_conflict') end
else
    -- Queued requests may complete with a known pre-dispatch rejection.
    if record.phase ~= 'inflight' and record.phase ~= 'queued' then return reply('request_not_started') end
    if record.phase == 'queued' and not input.not_dispatched then return reply('request_not_started') end
    if not input.failed then
        local position = input.transfer ~= cjson.null and input.transfer or nil
        local stream = record.stream
        if stream and (not position or stream.transfer_id ~= position.transfer_id or stream.sequence ~= position.sequence or
                       stream.offset ~= position.offset) then return reply('transfer_conflict') end
        if position and not stream and (position.sequence ~= 0 or position.offset ~= 0) then return reply('transfer_conflict') end
    end
    local cost = #response_json * 2 + 256
    if bytes + cost > byte_limit or redis.call('XLEN', responses) >= limits.response_frames then
        return reply('relay_overloaded')
    end
    redis.call('XADD', responses, '*', 'frame', response_json)
    record.phase, record.response_json, record.cost = 'completed', response_json, record.cost + cost
    redis.call('HSET', ledger, id, cjson.encode(record), '_bytes', bytes + cost)
end
redis.call('XACK', requests, 'owner', input.entry_id)
redis.call('XDEL', requests, input.entry_id)
return reply('ok', {phase = 'completed', entry_id = record.entry_id, response_json = record.response_json})
