# Python Frontmatter

[Front matter](https://docs.github.com/en/contributing/writing-for-github-docs/using-yaml-frontmatter) offers a useful way to add arbitrary, structured metadata to text documents, regardless of type.

This is a small package to load and parse files (or just text) with YAML (or JSON, TOML or other) front matter.

[![Tests](https://github.com/eyeseast/python-frontmatter/workflows/Test/badge.svg)](https://github.com/eyeseast/python-frontmatter/actions?query=workflow%3ATest)
[![PyPI](https://img.shields.io/pypi/v/python-frontmatter.svg)](https://pypi.org/project/python-frontmatter/)

**[Documentation](https://python-frontmatter.readthedocs.io/en/latest/)**

## Install:

```shell
# with pip
pip install python-frontmatter

# or uv
uv add python-frontmatter

# local development, with uv
uv sync
```

## Usage:

```python
>>> import frontmatter

```

Load a post from a filename:

```python
>>> post = frontmatter.load('tests/yaml/hello-world.txt')

```

Or a file (or file-like object):

```python
>>> with open('tests/yaml/hello-world.txt') as f:
...     post = frontmatter.load(f)

```

Or load from text:

```python
>>> with open('tests/yaml/hello-world.txt') as f:
...     post = frontmatter.loads(f.read())

```

If the file has a [Byte-Order Mark](https://en.wikipedia.org/wiki/Byte_order_mark) (BOM), strip it off first. An easy way to do this is by using the [`utf-8-sig`](https://docs.python.org/3/library/codecs.html?highlight=utf%208%20sig#module-encodings.utf_8_sig) encoding:

```python
>>> with open('tests/yaml/hello-world.txt', encoding="utf-8-sig") as f:
...     post = frontmatter.load(f)

```

Access content:

```python
>>> print(post.content)
Well, hello there, world.

# this works, too
>>> print(post)
Well, hello there, world.

```

Use metadata (metadata gets proxied as post keys):

```python
>>> print(post['title'])
Hello, world!

```

Metadata is a dictionary, with some handy proxies:

```python
>>> sorted(post.keys())
['layout', 'title']

>>> from pprint import pprint
>>> post['excerpt'] = 'tl;dr'
>>> pprint(post.metadata)
{'excerpt': 'tl;dr', 'layout': 'post', 'title': 'Hello, world!'}

```

If you don't need the whole post object, just parse:

```python
>>> with open('tests/yaml/hello-world.txt') as f:
...     metadata, content = frontmatter.parse(f.read())
>>> print(metadata['title'])
Hello, world!

```

Write back to plain text, too:

```python
>>> print(frontmatter.dumps(post)) # doctest: +NORMALIZE_WHITESPACE
---
excerpt: tl;dr
layout: post
title: Hello, world!
---
Well, hello there, world.

```

Or write to a file (or file-like object):

```python
>>> from io import StringIO
>>> f = StringIO()
>>> frontmatter.dump(post, f)
>>> print(f.getvalue()) # doctest: +NORMALIZE_WHITESPACE
---
excerpt: tl;dr
layout: post
title: Hello, world!
---
Well, hello there, world.

```

For more examples, see files in the `tests/` directory. Each sample file has a corresponding `.result.json` file showing the expected parsed output. See also the `examples/` directory, which covers more ways to customize input and output.

## Updating metadata without touching the body

The text API normalizes newlines and whitespace when round-tripping a document. If you only need to change metadata -- say, flipping a `published` flag -- and want the body left byte-for-byte intact, use the binary update API:

```python
>>> import frontmatter
>>> frontmatter.update_metadata_file('posts/hello.md', {'published': True})

```

This recognizes a UTF-8 BOM, detects the front matter format and rewrites only the metadata region. Everything after the closing delimiter is preserved exactly: no stripping, no newline conversion. The file is replaced atomically, only after the new content is complete.

There are also stream and bytes variants, which never touch the source:

```python
>>> with open('posts/hello.md', 'rb') as src, open('posts/hello.out.md', 'wb') as dst:
...     frontmatter.update_metadata_stream(src, dst, {'published': True})

>>> new_bytes = frontmatter.update_metadata_bytes(raw_bytes, {'published': True})

```

If a document has no front matter, pass `insert=True` to add a block; otherwise a `MissingFrontmatterError` is raised. Unclosed front matter raises `UnclosedFrontmatterError`, and unsupported encodings (UTF-16/UTF-32) raise `UnsupportedEncodingError` -- the file is never modified on a guess.
