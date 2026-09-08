-- All presentation mutations share the same admission and continuity checks.
-- A pending operation survives a runtime error: Redis does not roll Lua back.
local stream, metadata = KEYS[1], KEYS[2]
local request = cjson.decode(ARGV[1])
local operation = request.operation
local function refuse(code)
    error('RUN_STREAM_' .. code, 0)
end
local function field(name)
    return redis.call('HGET', metadata, name)
end
local function key_type(key)
    return redis.call('TYPE', key).ok
end
local stream_type, metadata_type = key_type(stream), key_type(metadata)
if (stream_type ~= 'none' and stream_type ~= 'stream') or
   (metadata_type ~= 'none' and metadata_type ~= 'hash') then
    refuse('CONTINUITY')
end

-- Memory deployments have an explicit process-local server identity supplied by
-- the storage adapter. Real Redis always obtains the primary incarnation here,
-- within the same script as its write, including after connection failover.
local server_id = request.memory_server_id
if server_id == '' then
    server_id = string.match(redis.call('INFO', 'server'), 'run_id:([^\r\n]+)')
end
if not server_id then refuse('CONTINUITY') end
if operation == 'initialize' and request.expected_server_id ~= server_id then refuse('CONTINUITY') end

local pending = field('pending')
if metadata_type ~= 'none' then
    if field('organization_id') ~= request.organization_id or field('run_id') ~= request.run_id then
        refuse('IDENTITY')
    end
    if field('publication_version') ~= '1' or field('server_id') ~= server_id then
        refuse('CONTINUITY')
    end
    if not pending then
        local tail = redis.call('XREVRANGE', stream, '+', '-', 'COUNT', 1)
        local last_id = #tail == 0 and '' or tail[1][1]
        if field('last_id') ~= last_id or tonumber(field('length')) ~= redis.call('XLEN', stream) then
            refuse('CONTINUITY')
        end
    end
elseif stream_type ~= 'none' then
    refuse('CONTINUITY')
elseif operation ~= 'initialize' or not request.allow_create then
    if operation == 'read' and request.after == '' then return {{}, {}, {}, {}} end
    refuse('CONTINUITY')
end

if operation == 'read' then
    if pending then refuse('PENDING') end
    local rows = redis.call('XRANGE', stream, request.after == '' and '-' or '(' .. request.after,
        '+', 'COUNT', request.limit)
    -- Deduplication receipts live as long as the active Run. Never return that
    -- growing hash in every bounded reader page.
    local summary = {}
    local function include(name)
        local value = field(name)
        if value then
            summary[#summary + 1] = name
            summary[#summary + 1] = value
        end
    end
    for _, name in ipairs({'organization_id', 'run_id', 'closed_at', 'trimmed', 'incomplete'}) do include(name) end
    for _, row in ipairs(rows) do
        for index = 1, #row[2], 2 do
            if row[2][index] == 'body' then
                local event = cjson.decode(row[2][index + 1])
                if event.event_type == 'run_attempt.running' and type(event.run_attempt_id) == 'string' then
                    include('attempt_projection:' .. event.run_attempt_id)
                end
            end
        end
    end
    return {
        summary,
        redis.call('XRANGE', stream, '-', '+', 'COUNT', 1),
        redis.call('XREVRANGE', stream, '+', '-', 'COUNT', 1),
        rows
    }
end

local function compare_fences(left, right)
    -- Attempt numbers are SQL bigint values; avoid Lua double rounding.
    if #left ~= #right then return #left < #right and -1 or 1 end
    if left == right then return 0 end
    return left < right and -1 or 1
end
local function active()
    return field('fence') == request.fence and field('attempt_id') == request.attempt_id
end
if request.attempt_owned and not active() then refuse('STALE') end

local receipt_key = 'activation:' .. (request.fence or '')
local receipt = field(receipt_key)
if operation == 'inspect' then
    if pending then refuse('PENDING') end
    if not receipt then return {} end
    local result = cjson.decode(receipt)
    if result.digest ~= request.digest then refuse('CONFLICT') end
    return {result.leased_id, result.recovery_id, active() and '1' or '0'}
end
if operation == 'activate' then
    if receipt then
        local result = cjson.decode(receipt)
        if result.digest ~= request.digest then refuse('CONFLICT') end
        if not pending then return {result.leased_id, result.recovery_id, active() and '1' or '0'} end
    else
        local comparison = compare_fences(request.fence, field('fence') or '0')
        if comparison < 0 or (comparison == 0 and not active()) then refuse('STALE') end
        if comparison == 0 then refuse('CONTINUITY') end
        if not request.allow_create then refuse('CONTINUITY') end
    end
end

-- Validate the complete bounded batch, including duplicate payloads, before
-- writing anything. Activation contains leased followed immediately by recovery.
local events = request.events or {}
local existing = {}
local missing_receipt = false
for _, event in ipairs(events) do
    if #event.body > request.max_event_bytes then refuse('SIZE') end
    local evidence = field('event:' .. event.id)
    if evidence then
        local previous = cjson.decode(evidence)
        if previous.digest ~= event.digest then refuse('CONFLICT') end
        existing[event.id] = previous
    else
        missing_receipt = true
    end
end
-- Confirming an already committed historical fact performs no mutation. It may
-- precede an unfinished close on retry without blocking recovery of that close.
if operation == 'lifecycle' and #events == 1 and existing[events[1].id] and pending ~= request.digest then
    return {existing[events[1].id].id}
end
if pending and pending ~= request.digest then refuse('PENDING') end
-- A runtime failure can interrupt XADD before its deduplication receipt. Such
-- writes follow the last committed tail and cannot have been trimmed: receipts
-- commit before retention. Only this bounded batch can follow that tail.
if pending and missing_receipt then
    local last_id = field('last_id')
    local rows = redis.call('XRANGE', stream, last_id == '' and '-' or '(' .. last_id,
        '+', 'COUNT', #events)
    for _, row in ipairs(rows) do
        local values = {}
        for index = 1, #row[2], 2 do values[row[2][index]] = row[2][index + 1] end
        for _, event in ipairs(events) do
            if values.event_id == event.id then
                if values.body ~= event.body then refuse('CONFLICT') end
                existing[event.id] = {id = row[1], digest = event.digest}
            end
        end
    end
end
local closed = field('closed_at')
if operation == 'initialize' then
    local initialized = field('initialization')
    if not initialized and (not request.allow_create or (metadata_type ~= 'none' and not pending)) then
        refuse('CONTINUITY')
    end
    if initialized then
        local result = cjson.decode(initialized)
        if result.digest ~= request.digest then refuse('CONFLICT') end
        if not pending then return {result.id} end
    end
end
if operation == 'complete' then
    local previous = field('attempt_projection:' .. request.attempt_id)
    if previous and previous ~= request.harness_run_id then refuse('PROJECTION_IDENTITY') end
    if previous == request.harness_run_id and not pending then return {'ok'} end
elseif operation == 'close' then
    if closed then
        if closed ~= request.closed_at then refuse('CLOSE_CONFLICT') end
        if not pending then return {'ok'} end
    end
end
if closed and not (operation == 'incomplete' and not request.attempt_owned) and not pending then
    refuse('CLOSED')
end
if (operation == 'append' or operation == 'activate' or operation == 'complete') and field('incomplete') == '1' then refuse('CONTINUITY') end

-- Install the barrier before any event, fence, completion, or retention change.
-- A failed write leaves this exact operation retryable and every other writer
-- and reader closed. Only finishing this batch removes the barrier.
if metadata_type == 'none' then
    redis.call('HSET', metadata, 'organization_id', request.organization_id, 'run_id', request.run_id,
        'publication_version', '1', 'server_id', server_id, 'last_id', '', 'length', '0',
        'pending', request.digest)
else
    redis.call('HSET', metadata, 'pending', request.digest)
end
local ids, updates = {}, {}
for _, event in ipairs(events) do
    local previous = existing[event.id]
    ids[#ids + 1] = previous and previous.id or
        redis.call('XADD', stream, '*', 'event_id', event.id, 'body', event.body)
    updates[#updates + 1] = 'event:' .. event.id
    updates[#updates + 1] = cjson.encode({id = ids[#ids], digest = event.digest})
end
local function update(name, value)
    updates[#updates + 1] = name
    updates[#updates + 1] = value
end
if operation == 'activate' then
    update('fence', request.fence)
    update('attempt_id', request.attempt_id)
    update(receipt_key, cjson.encode({digest = request.digest, leased_id = ids[1], recovery_id = ids[2] or ''}))
elseif operation == 'initialize' then
    update('initialization', cjson.encode({digest = request.digest, id = ids[1]}))
elseif operation == 'complete' then
    update('attempt_projection:' .. request.attempt_id, request.harness_run_id)
elseif operation == 'incomplete' then
    update('incomplete', '1')
elseif operation == 'close' then
    update('closed_at', request.closed_at)
end
-- Commit event receipts and publication state together before trimming. These
-- same receipts recover every completed batch, including already trimmed events.
redis.call('HSET', metadata, unpack(updates))
local length = redis.call('XLEN', stream)
if length > request.max_events then
    redis.call('HSET', metadata, 'trimmed', '1')
    redis.call('XTRIM', stream, 'MAXLEN', request.max_events)
end
local tail = redis.call('XREVRANGE', stream, '+', '-', 'COUNT', 1)
redis.call('HSET', metadata, 'last_id', #tail == 0 and '' or tail[1][1], 'length', redis.call('XLEN', stream))
if operation == 'close' then
    redis.call('EXPIRE', stream, request.closed_ttl_seconds)
    redis.call('EXPIRE', metadata, request.closed_ttl_seconds)
elseif not closed then
    redis.call('PERSIST', stream)
    redis.call('PERSIST', metadata)
end
redis.call('HDEL', metadata, 'pending', 'pending_ids')
if operation == 'activate' then return {ids[1], ids[2] or '', '1'} end
if #ids > 0 then return ids end
return {'ok'}
