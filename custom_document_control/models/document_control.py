# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import base64
import io
import csv
import openai
import re
import html  # Necesario para limpiar el historial

# ==========================================
# 1. MODELOS DE CONFIGURACIÓN
# ==========================================

class DocumentArea(models.Model):
    _name = 'document.area'
    _description = 'Áreas de la Empresa'
    name = fields.Char('Nombre del Área', required=True)
    code = fields.Char('Código (Abreviatura)', required=True, size=3, help="Ej: MKT, RRH")
    _sql_constraints = [('code_uniq', 'unique(code)', '¡El código de área debe ser único!')]

class DocumentCategory(models.Model):
    _name = 'document.category'
    _description = 'Categorías de Documentos'
    name = fields.Char('Nombre Categoría', required=True)
    code = fields.Char('Código', required=True, size=2, help="Ej: 01, AD, OP")

class DocumentType(models.Model):
    _name = 'document.type'
    _description = 'Tipos de Documento'
    name = fields.Char('Tipo de Documento', required=True)
    code = fields.Char('Código', required=True, size=2, help="Ej: PR, MN, PL")

class DocumentTag(models.Model):
    _name = 'document.tag'
    _description = 'Etiquetas de Documentos'
    name = fields.Char('Nombre', required=True)
    color = fields.Integer('Color')

# ==========================================
# 2. MODELO DE CARPETAS
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
    
    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for f in self:
            f.complete_name = '%s / %s' % (f.parent_id.complete_name, f.name) if f.parent_id else f.name

# ==========================================
# 3. MODELO PRINCIPAL (DOCUMENT CONTROL)
# ==========================================

class DocumentControl(models.Model):
    _name = 'document.control'
    _description = 'Control de Documentos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'code desc, version desc'

    # --- IDENTIFICACIÓN ---
    name = fields.Char(string='Título', required=True, tracking=True)
    code = fields.Char(string='Código', default='Borrador', readonly=True, index=True)
    
    # Campos Dinámicos
    area_id = fields.Many2one('document.area', string='Área', required=True, tracking=True)
    category_id = fields.Many2one('document.category', string='Categoría', tracking=True)
    type_id = fields.Many2one('document.type', string='Tipo Doc.', required=True, tracking=True)
    tag_ids = fields.Many2many('document.tag', string='Etiquetas')
    description = fields.Text(string='Descripción / Resumen')
    
    folder_id = fields.Many2one('document.folder', string='Carpeta', required=True, tracking=True)
    document_scope = fields.Selection([('internal', 'Interno ISO'),('external', 'Externo')], default='internal', required=True)
    sequence_number = fields.Integer(string='Secuencial', readonly=True)

    # --- VERSIONADO ---
    version = fields.Char(default='1.0', required=True, tracking=True)
    change_reason = fields.Text(string='Motivo del Cambio', tracking=True)
    source_document_id = fields.Many2one('document.control', readonly=True)
    active_revision_id = fields.Many2one('document.control', readonly=True)
    revision_type = fields.Selection([('major', 'Mayor'), ('minor', 'Menor')])
    
    state = fields.Selection([
        ('draft', 'Borrador'), ('upload', 'Carga'), ('review', 'En Revisión'),
        ('validate', 'En Aprobación'), ('approved', 'Publicado'),
        ('rejected', 'Rechazado'), ('obsolete', 'Obsoleto')
    ], default='draft', tracking=True)

    # --- ARCHIVOS ---
    editable_file = fields.Binary(string='Archivo Editable/Fuente', attachment=True)
    editable_filename = fields.Char(string='Nombre Editable')
    pdf_file = fields.Binary(string='Archivo Final (PDF/Video)', attachment=True)
    pdf_filename = fields.Char(string='Nombre Final')
    
    # Visor HTML
    preview_html = fields.Html(compute='_compute_preview_html', string='Visor', sanitize=False)

    # --- RESPONSABLES ---
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

    # ==========================================
    # 4. LÓGICA COMPUTADA Y VALIDACIONES
    # ==========================================

    @api.constrains('reviewer_ids', 'approver_ids')
    def _check_conflict_of_interest(self):
        for record in self:
            if self.env.user.has_group('custom_document_control.group_document_manager') or self.env.user.has_group('base.group_system'):
                continue
            if record.owner_id in record.reviewer_ids or record.owner_id in record.approver_ids:
                raise ValidationError("⛔ CONFLICTO DE INTERESES:\nNo puedes ser Juez y Parte. El propietario no puede auto-aprobarse.")

    @api.depends('owner_id')
    def _compute_is_owner(self):
        for record in self:
            record.is_owner = record.env.user == record.owner_id

    @api.depends('pdf_file', 'pdf_filename', 'editable_file', 'editable_filename')
    def _compute_preview_html(self):
        for record in self:
            record.preview_html = False
            content = ""
            if record.pdf_file and record.pdf_filename:
                file_url = f"/web/content/document.control/{record.id}/pdf_file"
                fname = record.pdf_filename.lower()
                if fname.endswith('.pdf'):
                    content = f'<iframe src="{file_url}" width="100%" height="85vh" style="border:none;"></iframe>'
                elif fname.endswith(('.mp4', '.webm')):
                    content = f'<div style="text-align:center; height:85vh; background:black; display:flex; align-items:center; justify-content:center;"><video controls style="max-width:100%; max-height:100%;"><source src="{file_url}" type="video/mp4"></video></div>'
                elif fname.endswith(('.jpg', '.png', '.jpeg', '.gif')):
                    content = f'<div style="text-align:center; height:85vh; display:flex; align-items:center; justify-content:center; overflow:auto;"><img src="{file_url}" style="max-width:100%; max-height:100%;"/></div>'
            elif record.editable_file and record.editable_filename:
                fname = record.editable_filename.lower()
                if fname.endswith('.csv'):
                    try:
                        csv_data = base64.b64decode(record.editable_file).decode('utf-8')
                        f = io.StringIO(csv_data)
                        reader = csv.reader(f, delimiter=',')
                        table_html = '<div style="overflow:auto; max-height:85vh;"><table class="table table-bordered table-striped" style="background:white; margin:0;">'
                        for i, row in enumerate(reader):
                            tag = 'th' if i == 0 else 'td'
                            table_html += '<tr>' + ''.join(f'<{tag} style="white-space:nowrap;">{cell}</{tag}>' for cell in row) + '</tr>'
                        table_html += '</table></div>'
                        content = table_html
                    except Exception as e:
                        content = f'<div class="alert alert-warning">Error al leer CSV: {str(e)}</div>'
            if content:
                record.preview_html = content
            else:
                record.preview_html = """<div class="alert alert-info text-center" style="margin-top:20px;"><h4>📂 Vista previa no disponible</h4><p>Descarga el archivo para verlo.</p></div>"""

    def _close_activity_for_current_user(self, feedback):
        domain = [('res_id', '=', self.id), ('res_model', '=', 'document.control'), ('user_id', '=', self.env.user.id)]
        self.env['mail.activity'].search(domain).action_feedback(feedback=feedback)

    def action_open_preview_popup(self):
        self.ensure_one()
        return {
            'name': 'Vista Previa: ' + self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'document.control',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('custom_document_control.view_document_preview_popup').id,
            'target': 'new',
            'flags': {'mode': 'readonly'},
        }

    # ---------------------------------------------------------
    # 🤖 IA GENERATIVA (SOLO DESCRIPCIÓN)
    # ---------------------------------------------------------
    def action_generate_ai_help(self):
        self.ensure_one()
        api_key = self.env['ir.config_parameter'].sudo().get_param('openai_api_key')
        if not api_key:
            raise ValidationError("⚠️ Falta la API Key de OpenAI en Ajustes.")
        
        info_doc = f"Título: {self.name}. Área: {self.area_id.name}. Tipo: {self.type_id.name}."
        prompt_system = "Eres un experto en Gestión de Calidad ISO 9001."
        prompt_user = (f"Basado en: '{info_doc}', escribe una 'Descripción/Resumen' profesional, técnica y corta "
                       "(máximo 2 líneas) que explique el propósito. Solo texto.")
        
        try:
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": prompt_system}, {"role": "user", "content": prompt_user}],
                temperature=0.7,
            )
            content = response.choices[0].message.content.strip().replace("Descripción:", "").replace('"', '').strip()
            self.description = content
        except Exception as e:
            raise ValidationError(f"Error IA: {str(e)}")

    # ---------------------------------------------------------
    # 🖨️ REPORTES, PDF Y GENERACIÓN ESTÁTICA
    # ---------------------------------------------------------
    
    def _generate_and_save_certificate(self):
        """ Genera el PDF de forma SEGURA y lo guarda """
        self.ensure_one()
        
        filename = f"Certificado - {self.code} - v{self.version}.pdf"
        
        # 1. Buscar si ya existe
        existing = self.env['ir.attachment'].search([
            ('res_model', '=', 'document.control'),
            ('res_id', '=', self.id),
            ('name', '=', filename)
        ], limit=1)
        if existing: return existing

        # 2. Buscar reporte PROTEGIENDO el error
        report_ref = 'custom_document_control.action_report_document_certificate'
        report_template = self.env.ref(report_ref, raise_if_not_found=False)

        # Si no existe, NO rompemos el sistema, solo avisamos en el log o retornamos False
        if not report_template:
            # Esto evita el "Record missing"
            print(f"⚠️ AVISO: No se encontró el reporte {report_ref}. No se generará el adjunto automático.")
            return False

        # 3. Generar
        try:
            pdf_content, _ = report_template._render_qweb_pdf(self.id)
            attachment = self.env['ir.attachment'].create({
                'name': filename, 'type': 'binary', 'datas': base64.b64encode(pdf_content),
                'res_model': 'document.control', 'res_id': self.id, 'mimetype': 'application/pdf'
            })
            return attachment
        except Exception as e:
            # Si falla renderizar, tampoco rompemos el flujo de aprobación
            print(f"⚠️ Error generando PDF: {str(e)}")
            return False

    def action_view_certificate(self):
        self.ensure_one()
        filename = f"Certificado - {self.code} - v{self.version}.pdf"
        attachment = self.env['ir.attachment'].search([('res_model', '=', 'document.control'), ('res_id', '=', self.id), ('name', '=', filename)], limit=1)

        if not attachment:
            attachment = self._generate_and_save_certificate()

        if attachment:
            return {'type': 'ir.actions.act_url', 'url': f'/web/content/{attachment.id}?download=false', 'target': 'new'}
        else:
            # Fallback si falla la generación: intentamos abrir el reporte dinámico
            return {
                'type': 'ir.actions.report',
                'report_name': 'custom_document_control.report_document_certificate_template',
                'report_type': 'qweb-pdf',
                'res_model': 'document.control',
                'res_ids': [self.id],
            }

    def get_full_audit_trail(self):
        """ Historial LIMPIO (sin HTML basura) """
        trail = []
        all_docs = self
        current = self
        while current.source_document_id:
            current = current.source_document_id
            all_docs += current
        
        for doc in all_docs:
            messages = self.env['mail.message'].search([
                ('model', '=', 'document.control'), ('res_id', '=', doc.id),
                ('message_type', 'in', ['comment', 'notification']), ('body', '!=', '')
            ], order='date desc')
            for msg in messages:
                # LIMPIEZA PROFUNDA DE HTML
                raw_text = html.unescape(msg.body or '')
                clean_text = re.sub('<[^<]+?>', ' ', raw_text)
                clean_text = " ".join(clean_text.split())
                
                if "Actividades pendientes" in clean_text: continue

                if clean_text:
                    trail.append({
                        'date': msg.date, 'version': doc.version,
                        'user': msg.author_id.name or 'Sistema',
                        'action': clean_text,
                        'type': 'reject' if 'Rechazado' in clean_text or 'Devuelto' in clean_text else 'info'
                    })
        return sorted(trail, key=lambda k: k['date'], reverse=True)

    # ---------------------------------------------------------
    # FLUJO Y ESTADOS
    # ---------------------------------------------------------
    def action_start_flow(self):
        self.ensure_one()
        if self.code == 'Borrador':
            area = self.area_id.code
            cat = self.category_id.code if self.category_id else 'GEN'
            tipo = self.type_id.code
            prefix = f"{area}-{cat}-{tipo}-"
            domain = [('code', 'like', prefix + '%')]
            last = self.search(domain, order='code desc', limit=1)
            new_seq = int(last.code.split('-')[-1]) + 1 if (last and last.code != 'Borrador') else 1
            self.code = f"{prefix}{new_seq:03d}"
            self.sequence_number = new_seq
        self.state = 'upload'
        self._close_activity_for_current_user("Carga iniciada.")

    def action_publish_direct(self):
        self.ensure_one()
        if self.document_scope != 'external': raise ValidationError("Solo para Externos.")
        self.write({'state': 'approved', 'issue_date': fields.Date.today(), 'approved_by_id': self.env.user.id, 'approval_date': fields.Datetime.now()})
        self._generate_and_save_certificate()
        self.message_post(body="🚀 Publicado.")
        self._close_activity_for_current_user("Publicado.")

    def action_submit_review(self):
        self.ensure_one()
        if self.document_scope == 'internal' and self.version != '1.0' and not self.change_reason:
            raise ValidationError("Falta Motivo del Cambio.")
        self._close_activity_for_current_user("Enviado a revisión.")
        if self.revision_type == 'minor':
            if not self.approver_ids: raise ValidationError("Asigna Aprobadores.")
            self.state = 'validate'
            for u in self.approver_ids:
                self.activity_schedule('mail.mail_activity_data_todo', user_id=u.id, note=f'Aprobación v{self.version}')
        else:
            if not self.reviewer_ids: raise ValidationError("Asigna Revisores.")
            self.state = 'review'
            for u in self.reviewer_ids:
                self.activity_schedule('mail.mail_activity_data_todo', user_id=u.id, note=f'Revisión v{self.version}')

    def action_review_pass(self):
        self.ensure_one()
        if self.env.user not in self.reviewer_ids and not self.env.user.has_group('base.group_system'):
            raise ValidationError("⛔ No tienes permiso. Solo los Revisores pueden dar el Visto Bueno.")
        self.write({'state': 'validate', 'reviewed_by_id': self.env.user.id, 'review_date': fields.Datetime.now()})
        self._close_activity_for_current_user("Visto Bueno.")
        for u in self.approver_ids:
            self.activity_schedule('mail.mail_activity_data_todo', user_id=u.id, note='Requiere Aprobación Final')

    def action_approve(self):
        self.ensure_one()
        if self.env.user not in self.approver_ids and not self.env.user.has_group('base.group_system'):
            raise ValidationError("⛔ No tienes permiso. Solo los Aprobadores pueden firmar.")
        self.write({'state': 'approved', 'issue_date': fields.Date.today(), 'approved_by_id': self.env.user.id, 'approval_date': fields.Datetime.now()})
        
        # Intentamos generar certificado, pero si falla NO ROMPEMOS la aprobación
        self._generate_and_save_certificate()
        
        self._close_activity_for_current_user("Aprobado.")
        if self.source_document_id:
            self.source_document_id.write({'state': 'obsolete', 'active_revision_id': False})

    def action_reject(self):
        self.ensure_one()
        return {
            'name': 'Indicar Motivo de Rechazo', 'type': 'ir.actions.act_window',
            'res_model': 'document.reject.wizard', 'view_mode': 'form',
            'target': 'new', 'context': {'default_document_id': self.id}
        }

    def _create_revision(self, rev_type):
        self.ensure_one()
        try: v = float(self.version)
        except: v = 1.0
        new_v = f"{int(v)+1}.0" if rev_type == 'major' else f"{v+0.1:.1f}"
        new_doc = self.copy({
            'version': new_v, 'state': 'upload', 'source_document_id': self.id,
            'code': self.code, 'revision_type': rev_type, 
            'editable_file': False, 'pdf_file': False, 'change_reason': False,
            'area_id': self.area_id.id, 'type_id': self.type_id.id, 'category_id': self.category_id.id
        })
        self.active_revision_id = new_doc.id
        return {'type': 'ir.actions.act_window', 'res_model': 'document.control', 'res_id': new_doc.id, 'view_mode': 'form', 'target': 'current'}

    def action_create_minor_rev(self): return self._create_revision('minor')
    def action_create_major_rev(self): return self._create_revision('major')
