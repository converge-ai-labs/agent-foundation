-- Only a committed terminal Run fact authorizes this unavailable-stream cleanup.
-- It never repairs history or grants publication authority, so an old primary
-- incarnation, missing Stream, or unfinished write cannot prevent retirement.
local stream, metadata = KEYS[1], KEYS[2]
local request = cjson.decode(ARGV[1])
local metadata_type = redis.call('TYPE', metadata).ok
if metadata_type == 'none' then
    if redis.call('EXISTS', stream) == 0 then return {'ok'} end
elseif metadata_type ~= 'hash' or
    redis.call('HGET', metadata, 'organization_id') ~= request.organization_id or
    redis.call('HGET', metadata, 'run_id') ~= request.run_id then
    error('RUN_STREAM_IDENTITY', 0)
end

if request.operation == 'schedule_expiry' then
    -- An absolute SQL-derived deadline also repairs crashes before TTL setup.
    -- Do not recreate a missing source, and never extend an existing deadline.
    local previous = tonumber(redis.call('HGET', metadata, 'recovery_deadline'))
    local deadline = previous and math.min(previous, request.deadline) or request.deadline
    if metadata_type ~= 'none' then
        redis.call('HSET', metadata, 'recovery_deadline', deadline)
    end
    -- A confirmed finalized snapshot releases the source to the independent
    -- raw replay policy, whose already-installed deadline remains unchanged.
    local retained = tonumber(redis.call('HGET', metadata, 'retention_deadline'))
    if retained then deadline = retained end
    for _, key in ipairs({stream, metadata}) do
        local expiry = redis.call('EXPIRETIME', key)
        redis.call('EXPIREAT', key, expiry >= 0 and math.min(expiry, deadline) or deadline)
    end
    return {'ok'}
end

-- Closing an incomplete source fences writers. Its suffix survives until
-- durable incomplete finalization or the fixed terminal recovery deadline.
redis.call('HSET', metadata, 'organization_id', request.organization_id, 'run_id', request.run_id,
    'closed_at', request.closed_at, 'incomplete', '1', 'attempt_id', '')
if type(request.recovery_deadline) == 'number' then
    local previous = tonumber(redis.call('HGET', metadata, 'recovery_deadline'))
    local deadline = previous and math.min(previous, request.recovery_deadline) or request.recovery_deadline
    redis.call('HSET', metadata, 'recovery_deadline', deadline)
    local retained = tonumber(redis.call('HGET', metadata, 'retention_deadline'))
    if retained then deadline = retained end
    redis.call('EXPIREAT', stream, deadline)
    redis.call('EXPIREAT', metadata, deadline)
end
redis.call('HDEL', metadata, 'pending')
if request.operation == 'release_retired' then
    local deadline = redis.call('HGET', metadata, 'retention_deadline') or
        tostring(tonumber(redis.call('TIME')[1]) + request.closed_ttl_seconds)
    redis.call('HSET', metadata, 'retention_deadline', deadline)
    redis.call('EXPIREAT', stream, deadline)
    redis.call('EXPIREAT', metadata, deadline)
end
return {'ok'}
