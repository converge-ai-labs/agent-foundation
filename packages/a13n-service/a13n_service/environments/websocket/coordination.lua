-- One bounded state document owns connection, takeover, and exclusive use.
-- Ticket consumption and candidate admission share this script. A rejected
-- candidate consumes its valid ticket but never changes an occupied slot.
local state_key, ticket_key = KEYS[1], KEYS[2]
local input = cjson.decode(ARGV[1])
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
local server_id = input.memory_server_id
if server_id == '' then
    server_id = string.match(redis.call('INFO', 'server'), 'run_id:([^\r\n]+)')
end
if not server_id then return redis.error_reply('Environment coordination identity unavailable') end
local function fail(code)
    return cjson.encode({code = code, now_ms = now})
end
local state_type, ticket_type = redis.call('TYPE', state_key).ok, redis.call('TYPE', ticket_key).ok
if (state_type ~= 'none' and state_type ~= 'string') or
   (ticket_type ~= 'none' and ticket_type ~= 'string') then
    return fail('coordination_unavailable')
end
local raw = redis.call('GET', state_key)
local state = raw and cjson.decode(raw) or nil
if state and (state.version ~= 1 or state.server_id ~= server_id) then state = nil end
local operation = input.operation
if not state then
    -- Even an empty Redis after data loss cannot prove that cached dispatch
    -- authority has expired. Wait the maximum grant horizon before new use.
    state = {version = 1, server_id = server_id, barrier_ms = now + input.lease_ms}
    if operation ~= 'issue' and operation ~= 'observe' then return fail('authority_lost') end
end
local function same(left, right)
    if not left or not right then return false end
    return left.organization_id == right.organization_id and left.environment_id == right.environment_id and
        left.connection_id == right.connection_id and left.connection_epoch == right.connection_epoch and
        left.owner_instance_id == right.owner_instance_id
end
local function same_use(left, right)
    return left and right and same(left.connection, right.connection) and left.use_id == right.use_id and
        left.run_id == right.run_id and left.attempt_id == right.attempt_id and
        left.attempt_fence == right.attempt_fence and left.worker_instance_id == right.worker_instance_id
end
local function persist()
    -- Keep retirement evidence longer than every granted local deadline, even
    -- when the candidate fails. Expiry must never precede a possible grant.
    redis.call('SET', state_key, cjson.encode(state), 'PX', input.retention_ms)
end
local function retired()
    return state.owner and state.retiring and same(state.owner.identity, state.retiring.identity)
end
local function owner_live()
    return state.owner and same(state.owner.identity, input.connection) and
        state.owner.expires_at_ms > now and not retired()
end
local function retire_owner()
    if state.owner then
        local until_ms = math.max(state.barrier_ms or 0, state.owner.expires_at_ms)
        if state.use then until_ms = math.max(until_ms, state.use.expires_at_ms) end
        if not retired() then
            state.retiring = {identity = state.owner.identity, until_ms = until_ms, acknowledged = false}
        end
        state.barrier_ms = math.max(state.barrier_ms or 0, state.retiring.until_ms)
        state.owner.online = false
    end
end
local function observation()
    local status, identity, expires = 'offline', cjson.null, 0
    if state.candidate and state.candidate.expires_at_ms > now then
        status, identity, expires = 'connecting', state.candidate.identity, state.candidate.expires_at_ms
    elseif state.owner and state.owner.expires_at_ms > now and not retired() then
        status = state.owner.online and 'online' or 'connecting'
        identity, expires = state.owner.identity, state.owner.expires_at_ms
    end
    return cjson.encode({
        code = 'ok', now_ms = now, status = status, connection = identity, expires_at_ms = expires,
        barrier_ms = state.barrier_ms or 0, retiring = state.retiring or cjson.null,
        use = state.use or cjson.null, error = state.error or cjson.null
    })
end

if operation == 'observe' then
    -- Reads do not extend a lost-history quarantine or a dead scope's retention.
    return observation()
elseif operation == 'issue' then
    local ticket = {connection_id = input.connection_id, organization_id = input.organization_id,
        environment_id = input.environment_id, expires_at_ms = now + input.ticket_ms}
    if not redis.call('SET', ticket_key, cjson.encode(ticket), 'PX', input.ticket_ms, 'NX') then
        return fail('ticket_conflict')
    end
    persist()
    return cjson.encode({code = 'ok', now_ms = now, expires_at_ms = ticket.expires_at_ms})
elseif operation == 'admit' then
    local ticket_raw = redis.call('GET', ticket_key)
    if not ticket_raw then return fail('ticket_invalid') end
    local ticket = cjson.decode(ticket_raw)
    if ticket.organization_id ~= input.connection.organization_id or
       ticket.environment_id ~= input.connection.environment_id or ticket.expires_at_ms <= now then
        return fail('ticket_invalid')
    end
    redis.call('DEL', ticket_key)
    if state.candidate and state.candidate.expires_at_ms > now then return fail('candidate_busy') end
    input.connection.connection_id = ticket.connection_id
    retire_owner()
    state.candidate = {identity = input.connection, expires_at_ms = now + input.candidate_ms}
    state.error = nil
elseif operation == 'promote' then
    if not state.candidate or not same(state.candidate.identity, input.connection) or
       state.candidate.expires_at_ms <= now then return fail('candidate_expired') end
    if (state.barrier_ms or 0) > now and not (state.retiring and state.retiring.acknowledged) then
        return fail('handover_pending')
    end
    state.owner = {identity = state.candidate.identity, expires_at_ms = now + input.lease_ms, online = false}
    state.candidate, state.retiring, state.use, state.error = nil, nil, nil, nil
    state.barrier_ms = 0
elseif operation == 'online' or operation == 'renew' then
    if not owner_live() then return fail('authority_lost') end
    state.owner.expires_at_ms = now + input.lease_ms
    if operation == 'online' then state.owner.online = true end
elseif operation == 'acquire_use' then
    if not owner_live() or not state.owner.online then return fail('authority_lost') end
    if state.use then
        if same_use(state.use.identity, input.use) and state.use.expires_at_ms > now then
            return observation()
        end
        return fail('environment_busy')
    end
    if not same(input.use.connection, input.connection) then return fail('authority_lost') end
    local expires = math.min(now + input.lease_ms, state.owner.expires_at_ms, input.attempt_expires_at_ms)
    if expires <= now then return fail('authority_lost') end
    state.use = {identity = input.use, expires_at_ms = expires}
elseif operation == 'renew_use' then
    if not owner_live() or not state.owner.online or not state.use or
       not same_use(state.use.identity, input.use) or state.use.expires_at_ms <= now then
        return fail('authority_lost')
    end
    local expires = math.min(now + input.lease_ms, state.owner.expires_at_ms, input.attempt_expires_at_ms)
    if expires <= now then return fail('authority_lost') end
    state.use.expires_at_ms = expires
elseif operation == 'release_use' then
    if not state.owner or not same(state.owner.identity, input.connection) or
       not state.use or not same_use(state.use.identity, input.use) then
        return fail('authority_lost')
    end
    retire_owner()
    state.error = 'environment_unavailable'
elseif operation == 'retire' then
    if not state.owner or not same(state.owner.identity, input.connection) then return fail('authority_lost') end
    retire_owner()
    state.error = input.error
elseif operation == 'acknowledge' then
    if not state.retiring or not same(state.retiring.identity, input.connection) then
        return fail('authority_lost')
    end
    -- Only the socket owner calls this after irreversibly fencing and detaching.
    state.retiring.acknowledged = true
elseif operation == 'abandon' then
    if not state.candidate or not same(state.candidate.identity, input.connection) then
        return fail('candidate_expired')
    end
    state.candidate = nil
    state.error = input.error
else
    return fail('operation_invalid')
end
persist()
return observation()
