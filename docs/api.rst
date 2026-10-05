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


Updating metadata in place
--------------------------

These functions rewrite only the front matter region of a document,
preserving the body byte-for-byte. They operate on bytes and binary
streams rather than text.

.. autofunction:: frontmatter.update_metadata_bytes

.. autofunction:: frontmatter.update_metadata_stream

.. autofunction:: frontmatter.update_metadata_file

.. autoexception:: frontmatter.FrontmatterUpdateError

.. autoexception:: frontmatter.UnsupportedEncodingError

.. autoexception:: frontmatter.MissingFrontmatterError

.. autoexception:: frontmatter.UnclosedFrontmatterError


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
