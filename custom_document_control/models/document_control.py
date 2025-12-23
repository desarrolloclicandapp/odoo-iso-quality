# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import base64
import io
import csv
import openai
import re
import html

try:
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
except ImportError:
    PdfReader = None

# ==========================================
# 1. CONFIGURACIÓN
# ==========================================
class DocumentArea(models.Model):
    _name = 'document.area'
    _description = 'Áreas'
    name = fields.Char('Nombre', required=True)
    code = fields.Char('Código', required=True, size=3)
    _sql_constraints = [('code_uniq', 'unique(code)', '¡Código único!')]

class DocumentCategory(models.Model):
    _name = 'document.category'
    _description = 'Categorías'
    name = fields.Char('Nombre', required=True)
    code = fields.Char('Código', required=True, size=2)

class DocumentType(models.Model):
    _name = 'document.type'
    _description = 'Tipos'
    name = fields.Char('Tipo', required=True)
    code = fields.Char('Código', required=True, size=3)

class DocumentTag(models.Model):
    _name = 'document.tag'
    _description = 'Etiquetas'
    name = fields.Char('Nombre', required=True)
    color = fields.Integer('Color')

# ==========================================
# 2. CARPETAS (LÓGICA DE AYER: GRUPOS)
# ==========================================
class DocumentFolder(models.Model):
    _name = 'document.folder'
    _description = 'Carpetas'
    _parent_store = True
    _rec_name = 'complete_name'
    
    name = fields.Char(required=True)
    parent_id = fields.Many2one('document.folder', ondelete='cascade', index=True)
    parent_path = fields.Char(index=True, unaccent=False)
    complete_name = fields.Char(compute='_compute_complete_name', store=True)
    child_ids = fields.One2many('document.folder', 'parent_id')
    
    # SEGURIDAD DE AYER (Por Grupos)
    allowed_group_ids = fields.Many2many('res.groups', string='Grupos con Acceso')
    # Campo Puente calculado (Vital para la regla de seguridad de ayer)
    access_user_ids = fields.Many2many('res.users', compute='_compute_access_user_ids', store=True)
    
    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for f in self:
            f.complete_name = '%s / %s' % (f.parent_id.complete_name, f.name) if f.parent_id else f.name

    @api.depends('allowed_group_ids')
    def _compute_access_user_ids(self):
        for f in self:
            if f.allowed_group_ids:
                users = self.env['res.users'].search([('groups_id', 'in', f.allowed_group_ids.ids)])
                f.access_user_ids = users
            else:
                f.access_user_ids = False

# ==========================================
# 3. DOCUMENT CONTROL (VERSIÓN AYER)
# ==========================================
class DocumentControl(models.Model):
    _name = 'document.control'
    _description = 'Control de Documentos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'code desc, version desc'

    name = fields.Char(string='Título', required=True, tracking=True)
    code = fields.Char(string='Código', default='Borrador', readonly=True, index=True)
    
    area_id = fields.Many2one('document.area', string='Área', required=True, tracking=True)
    category_id = fields.Many2one('document.category', string='Categoría', tracking=True)
    type_id = fields.Many2one('document.type', string='Tipo', required=True, tracking=True)
    tag_ids = fields.Many2many('document.tag', string='Etiquetas')
    description = fields.Text(string='Descripción')
    
    folder_id = fields.Many2one('document.folder', string='Carpeta', required=True, tracking=True)
    document_scope = fields.Selection([('internal', 'Interno'),('external', 'Externo')], default='internal', required=True)
    sequence_number = fields.Integer(readonly=True)

    version = fields.Char(default='1.0', required=True, tracking=True)
    change_reason = fields.Text(tracking=True)
    source_document_id = fields.Many2one('document.control', readonly=True)
    active_revision_id = fields.Many2one('document.control', readonly=True)
    revision_type = fields.Selection([('major', 'Mayor'), ('minor', 'Menor')])
    
    history_ids = fields.Many2many('document.control', compute='_compute_history_ids', string='Historial')

    state = fields.Selection([
        ('draft', 'Borrador'), ('upload', 'Carga'), ('review', 'Revisión'),
        ('validate', 'Aprobación'), ('approved', 'Publicado'),
        ('rejected', 'Rechazado'), ('obsolete', 'Obsoleto')
    ], default='draft', tracking=True)

    editable_file = fields.Binary(attachment=True)
    editable_filename = fields.Char()
    pdf_file = fields.Binary(attachment=True)
    pdf_filename = fields.Char()
    preview_html = fields.Html(compute='_compute_preview_html', sanitize=False)

    owner_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    reviewer_ids = fields.Many2many('res.users', 'doc_rev_rel', string='Revisores')
    approver_ids = fields.Many2many('res.users', 'doc_app_rel', string='Aprobadores')
    
    reviewed_by_id = fields.Many2one('res.users', readonly=True)
    review_date = fields.Datetime(readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    approval_date = fields.Datetime(readonly=True)
    
    issue_date = fields.Date(string='Fecha Emisión')
    is_owner = fields.Boolean(compute='_compute_is_owner')

    _sql_constraints = [('code_version_uniq', 'unique(code, version)', '¡Versión duplicada!')]

    @api.depends('code')
    def _compute_history_ids(self):
        for r in self:
            if r.code and r.code != 'Borrador':
                r.history_ids = self.search([('code', '=', r.code), ('id', '!=', r.id)], order='version desc')
            else:
                r.history_ids = False

    @api.constrains('reviewer_ids', 'approver_ids')
    def _check_conflict(self):
        for r in self:
            if self.env.user.has_group('base.group_system'): continue
            if r.owner_id in r.reviewer_ids or r.owner_id in r.approver_ids:
                raise ValidationError("⛔ El propietario no puede auto-aprobarse.")

    @api.depends('owner_id')
    def _compute_is_owner(self):
        for r in self: r.is_owner = r.env.user == r.owner_id

    @api.depends('pdf_file', 'pdf_filename', 'editable_file')
    def _compute_preview_html(self):
        for r in self:
            r.preview_html = False
            if r.pdf_file and r.pdf_filename:
                url = f"/web/content/document.control/{r.id}/pdf_file"
                if r.pdf_filename.lower().endswith('.pdf'):
                    r.preview_html = f'<iframe src="{url}" width="100%" height="85vh" style="border:none;"></iframe>'
                else:
                    r.preview_html = f'<div class="text-center p-3"><a href="{url}" class="btn btn-primary">Descargar</a></div>'
            elif r.editable_file:
                r.preview_html = '<div class="alert alert-info">Archivo fuente disponible para descarga.</div>'

    def action_generate_ai_help(self):
        self.ensure_one()
        key = self.env['ir.config_parameter'].sudo().get_param('openai_api_key')
        if not key: raise ValidationError("Falta API Key")
        try:
            client = openai.OpenAI(api_key=key)
            resp = client.chat.completions.create(model="gpt-3.5-turbo", messages=[
                {"role": "system", "content": "Experto ISO."},
                {"role": "user", "content": f"Resumen corto para: {self.name}"}
            ])
            self.description = resp.choices[0].message.content
        except Exception as e: raise ValidationError(str(e))

    def _apply_watermark(self, text, prefix):
        if not PdfReader or not self.pdf_file: return
        try:
            data = base64.b64decode(self.pdf_file)
            reader = PdfReader(io.BytesIO(data))
            writer = PdfWriter()
            packet = io.BytesIO()
            c = canvas.Canvas(packet, pagesize=letter)
            c.setFont("Helvetica-Bold", 50)
            c.setFillColorRGB(0.5, 0.5, 0.5, 0.2)
            c.saveState()
            c.translate(300, 400); c.rotate(45); c.drawCentredString(0, 0, text)
            c.restoreState()
            c.save()
            packet.seek(0)
            water = PdfReader(packet)
            for page in reader.pages:
                page.merge_page(water.pages[0])
                writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
            self.write({'pdf_file': base64.b64encode(out.getvalue()), 'pdf_filename': f"{prefix} - {self.pdf_filename}"})
        except: pass

    def _generate_certificate(self):
        self.ensure_one()
        fname = f"Certificado - {self.code} - v{self.version}.pdf"
        if self.env['ir.attachment'].search([('name', '=', fname), ('res_id', '=', self.id)]): return
        try:
            pdf, _ = self.env.ref('custom_document_control.action_report_document_certificate')._render_qweb_pdf(self.id)
            self.env['ir.attachment'].create({'name': fname, 'datas': base64.b64encode(pdf), 'res_model': 'document.control', 'res_id': self.id})
        except: pass

    def action_view_certificate(self):
        self._generate_certificate()
        return {'type': 'ir.actions.report', 'report_name': 'custom_document_control.report_document_certificate_template', 'res_model': 'document.control', 'res_ids': [self.id]}

    def action_start_flow(self):
        if self.code == 'Borrador':
            prefix = f"{self.area_id.code}-{self.category_id.code or 'EXT'}-{self.type_id.code}-"
            last = self.search([('code', 'like', prefix + '%')], order='code desc', limit=1)
            seq = int(last.code.split('-')[-1]) + 1 if (last and last.code != 'Borrador') else 1
            self.code, self.sequence_number = f"{prefix}{seq:03d}", seq
        self.state = 'upload'

    def action_publish_direct(self):
        self.write({'state': 'approved', 'issue_date': fields.Date.today()})
        self._generate_certificate()

    def action_submit_review(self):
        if self.revision_type == 'minor' and not self.approver_ids: raise ValidationError("Faltan Aprobadores")
        if self.revision_type == 'major' and not self.reviewer_ids: raise ValidationError("Faltan Revisores")
        self.state = 'validate' if self.revision_type == 'minor' else 'review'

    def action_review_pass(self):
        self.write({'state': 'validate', 'reviewed_by_id': self.env.user.id, 'review_date': fields.Datetime.now()})

    def action_approve(self):
        self._apply_watermark("COPIA CONTROLADA", "APROBADO")
        self.write({'state': 'approved', 'issue_date': fields.Date.today(), 'approved_by_id': self.env.user.id, 'approval_date': fields.Datetime.now()})
        self._generate_certificate()
        if self.source_document_id: self.source_document_id.write({'state': 'obsolete', 'active_revision_id': False})

    def action_reject(self):
        return {'name': 'Rechazar', 'type': 'ir.actions.act_window', 'res_model': 'document.reject.wizard', 'view_mode': 'form', 'target': 'new', 'context': {'default_document_id': self.id}}

    def _create_rev(self, t):
        self._apply_watermark("OBSOLETO", "OBSOLETO")
        v = float(self.version) if self.version.replace('.','').isdigit() else 1.0
        nv = f"{int(v)+1}.0" if t == 'major' else f"{v+0.1:.1f}"
        new = self.copy({'version': nv, 'state': 'upload', 'source_document_id': self.id, 'revision_type': t, 'editable_file': False, 'pdf_file': False})
        self.active_revision_id = new.id
        return {'type': 'ir.actions.act_window', 'res_model': 'document.control', 'res_id': new.id, 'view_mode': 'form', 'target': 'current'}

    def action_create_minor_rev(self): return self._create_rev('minor')
    def action_create_major_rev(self): return self._create_rev('major')
    def action_open_from_list(self): return {'type': 'ir.actions.act_window', 'res_model': 'document.control', 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}
