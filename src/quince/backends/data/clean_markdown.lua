--[[
Quince: clean pandoc's document model before writing Markdown (pandoc 2.9 Lua API).

What Word, LibreOffice and pandoc's readers leave behind that has no place in Markdown:
- bookmark and anchor spans (`<span id="anchor"></span>`), and wrapper divs;
- block quotes inside list items (indentation read as a quote);
- loose list items made of a single paragraph (blank lines between every item);
- one list split in two because the numbering definition changed;
- tables whose header row is empty because the file did not mark a header row;
- image sizes (with them, GFM can only write an HTML <img>, which is not wanted here);
- a caption paragraph repeating the text of the image right above it;
- a quote inside a quote (RTF indentation read twice).
]]

local function unwrap_quotes(blocks)
  local out = {}
  for _, block in ipairs(blocks) do
    if block.t == "BlockQuote" then
      for _, inner in ipairs(unwrap_quotes(block.content)) do
        table.insert(out, inner)
      end
    else
      table.insert(out, block)
    end
  end
  return out
end

local merge_lists  -- defined below; list items are cleaned recursively

local function clean_items(items)
  local tight = true
  for i, item in ipairs(items) do
    item = merge_lists(unwrap_quotes(item))
    items[i] = item
    local paras = 0
    for _, block in ipairs(item) do
      if block.t == "Para" then paras = paras + 1 end
    end
    if paras > 1 then tight = false end
  end
  if tight then
    for _, item in ipairs(items) do
      for j, block in ipairs(item) do
        if block.t == "Para" then item[j] = pandoc.Plain(block.content) end
      end
    end
  end
  return items
end

-- Adjacent lists of the same kind are one list (Word's numbering changes split them).
local function continues(last, block)
  if not last or last.t ~= block.t then return false end
  if block.t == "BulletList" then return true end
  return block.t == "OrderedList"
    and block.listAttributes[2] == last.listAttributes[2]
    and block.listAttributes[1] == 1
end

merge_lists = function(blocks)
  local out = {}
  for _, block in ipairs(blocks) do
    local last = out[#out]
    if continues(last, block) then
      for _, item in ipairs(block.content) do
        table.insert(last.content, item)
      end
    else
      table.insert(out, block)
    end
  end
  return out
end

function Span(el)
  return el.content
end

function Div(el)
  return el.content
end

function Image(el)
  el.attr = pandoc.Attr()
  return el
end

function BlockQuote(el)
  if #el.content == 1 and el.content[1].t == "BlockQuote" then
    return el.content[1]
  end
  return el
end

function Header(el)
  el.attr = pandoc.Attr()
  return el
end

function BulletList(el)
  el.content = clean_items(el.content)
  return el
end

function OrderedList(el)
  el.content = clean_items(el.content)
  return el
end

local function cell_is_empty(cell)
  return #cell == 0 or pandoc.utils.stringify(pandoc.Div(cell)) == ""
end

function Table(el)
  local empty = true
  for _, cell in ipairs(el.headers) do
    if not cell_is_empty(cell) then empty = false end
  end
  if empty and #el.rows > 0 then
    el.headers = table.remove(el.rows, 1)
  end
  return el
end

-- A paragraph that is only an image, followed by a paragraph with the same text as the
-- image description (Word's and pandoc's figure captions): the caption is dropped.
local function image_text(block)
  if (block.t == "Para" or block.t == "Plain") and #block.content == 1
      and block.content[1].t == "Image" then
    return pandoc.utils.stringify(block.content[1].caption)
  end
  return nil
end

local function drop_repeated_captions(blocks)
  local out = {}
  for _, block in ipairs(blocks) do
    local last = out[#out]
    local caption = last and image_text(last)
    if not (caption and caption ~= "" and (block.t == "Para" or block.t == "Plain")
        and pandoc.utils.stringify(block) == caption) then
      table.insert(out, block)
    end
  end
  return out
end

function Pandoc(doc)
  doc.blocks = drop_repeated_captions(merge_lists(doc.blocks))
  return doc
end
