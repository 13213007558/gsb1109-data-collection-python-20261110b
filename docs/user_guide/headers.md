(headers)=
# {fas}`file-code` Cache Headers
Requests-cache supports most common HTTP caching headers, including
[ETags](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/ETag),
[Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cache-Control),
and several extensions.

(conditional-requests)=
## Conditional Requests
[Conditional requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Conditional_requests) are
automatically sent for any servers that support them. Once a cached response expires, it will only
be updated if the remote content has changed.

Here's an example using the [GitHub API](https://docs.github.com/en/rest) to get info about the
requests-cache repo:
```python
>>> # Cache a response that will expire immediately
>>> url = 'https://api.github.com/repos/requests-cache/requests-cache'
>>> session = CachedSession(expire_after=1)
>>> session.get(url)
>>> time.sleep(1)

>>> # The cached response will still be used until the remote content actually changes
>>> response = session.get(url)
>>> print(response.from_cache)
True
```

### Caller-supplied validation headers
If you send your own `If-None-Match` or `If-Modified-Since` headers (for example, a sync program
that tracks version numbers itself), those headers are always forwarded to the server exactly as
you set them. requests-cache will not replace them with the validators of a cached response, even
when the values happen to be identical.

The two sources of validation stay independent:

* A `304 Not Modified` returned for **your own** conditional request is passed back to you with its
  `304` status, while the matching cached entry is still revalidated in the background.
* A `304` produced by the cache's **own** revalidation (when you did not supply validation headers)
  continues to return the refreshed cached response as usual.
* If the server returns a `304` when there is no matching cached response, it is returned as-is and
  is **not** stored, since a `304` has no body and cannot be turned into a cached representation.

```{note}
Also see {ref}`stale-while-revalidate` for a variation of this behavior.
```

(cache-control)=
## Cache-Control
`Cache-Control` **request** headers will always be used if present. This is mainly useful if you are
adding requests-cache to an existing application or library that already sends requests with cache
headers.

`Cache-Control` **response** headers are an opt-in feature. If enabled, these will take priority over
any other `expire_after` values. See {ref}`precedence` for the full order of precedence.
To enable this behavior, use the `cache_control` option:
```python
>>> session = CachedSession(cache_control=True)
```

## Supported Headers
Requests-cache implements the majority of private cache behaviors specified by the following RFCs,
with some minor variations:
* [RFC 2616](https://datatracker.ietf.org/doc/html/rfc2616)
* [RFC 5861](https://datatracker.ietf.org/doc/html/rfc5861)
* [RFC 7234](https://datatracker.ietf.org/doc/html/rfc7234)
* [RFC 8246](https://datatracker.ietf.org/doc/html/rfc8246)

The following headers are currently supported:

**Request headers:**
- `Cache-Control: max-age`: Used as the expiration time in seconds
- `Cache-Control: max-stale`: Accept responses that have been expired for up to this many seconds
- `Cache-Control: min-fresh`: Don't accept responses if they will expire within this many seconds
- `Cache-Control: no-cache`: Revalidate with the server before using a cached response
- `Cache-Control: no-store`: Skip reading from and writing to the cache
- `Cache-Control: only-if-cached`: Only return results from the cache. If not cached, return a 504
  response instead of sending a new request. Note that this may return a stale response.
- `Cache-Control: stale-if-error`: If an error occurs while refreshing a cached response, use it
  if it expired by no more than this many seconds ago
- `If-None-Match`: Automatically added for revalidation, if an `ETag` is available. A value you set
  yourself is always preserved and never replaced
- `If-Modified-Since`: Automatically added for revalidation, if `Last-Modified` is available. A
  value you set yourself is always preserved and never replaced

**Response headers:**
- `Cache-Control: immutable`: Cache the response with no expiration
- `Cache-Control: max-age`: Used as the expiration time in seconds
- `Cache-Control: must-revalidate`: When used in combination with `max-age=0`, revalidate immediately.
- `Cache-Control: no-cache`: Revalidate with the server before using a cached response
- `Cache-Control: no-store` Skip writing to the cache
- `Cache-Control: stale-if-error`: Same behavior as request header
- `Cache-Control: stale-while-revalidate`: If expired by less than this many seconds, return the stale response immediately and send an asynchronous revalidation request
- `Expires`: Used as an absolute expiration datetime
- `ETag`: Validator used for conditional requests
- `Last-Modified`: Validator used for conditional requests
- `Vary`: Used to indicate which request headers to match. See {ref}`matching-headers` for details.
