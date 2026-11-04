--[[
Quince: pandoc never fetches anything (Hard Rule 3; it also runs without network). A remote
image cannot be embedded in DOCX, ODT, EPUB or a self-contained HTML page, so it becomes a
link to the image, and Quince notes this on the result card.
]]

local remote = false

function Image(el)
  if el.src:match("^https?://") or el.src:match("^//") then
    remote = true
    local text = el.caption
    if #text == 0 then text = {pandoc.Str(el.src)} end
    return pandoc.Link(text, el.src)
  end
end

function Pandoc(doc)
  if remote then io.stderr:write("QUINCE:remote-images\n") end
  return doc
end
