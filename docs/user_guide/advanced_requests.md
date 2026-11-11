# {fas}`info-circle` Advanced Requests
Following are some tips on using requests-cache with some of the more
[advanced features](https://requests.readthedocs.io/en/latest/user/advanced/) of the requests
library.

## Event Hooks
Requests has an [event hook](https://requests.readthedocs.io/en/latest/user/advanced/#event-hooks)
system that can be used to add custom behavior into different parts of the request process.
It can be used, for example, for request throttling:

:::{dropdown} Example
:animate: fade-in-slide-down
:color: primary
:icon: file-code

```python
>>> import time
>>> import requests
>>> from requests_cache import CachedSession

>>> def make_throttle_hook(timeout=1.0):
>>>     """Make a request hook function that adds a custom delay for non-cached requests"""
>>>     def hook(response, *args, **kwargs):
>>>         if not getattr(response, 'from_cache', False):
>>>             print('sleeping')
>>>             time.sleep(timeout)
>>>         return response
>>>     return hook

>>> session = CachedSession()
>>> session.hooks['response'].append(make_throttle_hook(0.1))
>>> # The first (real) request will have an added delay
>>> session.get('https://httpbin.org/get')
>>> session.get('https://httpbin.org/get')
```
:::

## Streaming Requests
If you use [streaming requests](https://requests.readthedocs.io/en/latest/user/advanced/#id9), you
can use the same code to iterate over both cached and non-cached requests. Cached response content
will have already been read (i.e., consumed), but will be available for re-reading so it behaves like
the original streamed response:

:::{dropdown} Example
:animate: fade-in-slide-down
:color: primary
:icon: file-code

```python
>>> from requests_cache import CachedSession

>>> session = CachedSession()
>>> for i in range(2):
...     response = session.get('https://httpbin.org/stream/20', stream=True)
...     for chunk in response.iter_lines():
...         print(chunk.decode('utf-8'))
```
:::

### Deferred caching for streams
By default, a streamed response is fully buffered (and cached) as soon as it is received, even if
you only read part of it. If you instead want to consume the response as a stream right away and
only cache it once it has been **completely** read, use the `lazy_streams` option:

:::{dropdown} Example
:animate: fade-in-slide-down
:color: primary
:icon: file-code

```python
>>> session = CachedSession(lazy_streams=True)
>>> response = session.get('https://httpbin.org/stream/20', stream=True)
>>> # Not cached yet; the response is cached only after the body is fully consumed
>>> for chunk in response.iter_content(chunk_size=1024):
...     process(chunk)
```
:::

With `lazy_streams` enabled, a response will not be cached if you close it (or otherwise stop
reading) before the stream is finished, or if the download fails partway through. This guarantees
that the cache never contains a partially downloaded response.

You can also set `max_cacheable_size` (in bytes) to avoid caching very large responses. Responses
larger than this are simply not cached; this does not interrupt streaming:

```python
>>> session = CachedSession(lazy_streams=True, max_cacheable_size=1024**2)
```
