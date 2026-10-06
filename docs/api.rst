API
====

.. module:: frontmatter

Reading
-------

.. autofunction:: frontmatter.parse

.. autofunction:: frontmatter.check

.. autofunction:: frontmatter.checks

.. autofunction:: frontmatter.load

.. autofunction:: frontmatter.loads


Writing
-------

.. autofunction:: frontmatter.dump

.. autofunction:: frontmatter.dumps


Converting between formats
--------------------------

Front matter can be converted between YAML, JSON and TOML. Before
anything is written, every field is checked against the target
format's value domain, so silent data loss -- dates dropped from JSON,
nulls dropped from TOML, keys coerced to strings, or unknown objects
stringified -- is reported instead of passing for success.

.. autoclass:: frontmatter.TargetFormat

.. autoclass:: frontmatter.IncompatibilityReason

.. autoclass:: frontmatter.IncompatibleField
    :members:

.. autoclass:: frontmatter.ConversionReport
    :members:

.. autoclass:: frontmatter.ConversionError
    :members:

.. autoclass:: frontmatter.ConvertedMetadata
    :members:

.. autoclass:: frontmatter.ConvertedText
    :members:

.. autofunction:: frontmatter.check_metadata

.. autofunction:: frontmatter.convert_metadata

.. autofunction:: frontmatter.convert_post

.. autofunction:: frontmatter.convert_text


Post objects
------------

.. autoclass:: frontmatter.Post
    :members:
    :special-members: __getitem__, __setitem__, __delitem__


Handlers
--------

.. autoclass:: frontmatter.default_handlers.BaseHandler
    :members: 

.. autoclass:: frontmatter.default_handlers.YAMLHandler

.. autoclass:: frontmatter.default_handlers.JSONHandler

.. autoclass:: frontmatter.default_handlers.TOMLHandler
