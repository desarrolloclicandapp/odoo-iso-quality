# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

# --- MODELO CARPETAS (Sin cambios) ---
class DocumentFolder(models.Model):
    _name = 'document.folder'
    _description = 'Carpetas de Documentos'
    _parent_store = True
    _order = 'complete_name'
    _rec_name = 'complete_name'

    name = fields.Char(string='Nombre de Carpeta', required=True)
    parent_id = fields.Many2one('document.folder', string='Carpeta Padre', ondelete='cascade', index=True)
    parent_path = fields.Char(index=True, unaccent=False)
    complete_name = fields.Char('Ruta Completa', compute='_compute_complete_name', store=True)
    child_ids = fields.One2many('document.folder', 'parent_id', string='Subcarpetas')

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for folder in self:
            if folder.parent_id:
                folder.complete_name = '%s / %s' % (folder.parent_id.complete_name, folder.name)
            else:
                folder.complete_name = folder.name

# --- MODELO DOCUMENTOS ---
class DocumentControl(models.Model):
    _name = 'document.control'
    _description = 'Control de Documentos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'code desc, version desc'

    # --- 1. Identificación ---
    name = fields.Char(string='Título', required=True, tracking=True)
    code = fields.Char(string='Código', default='Nuevo', readonly=True, index=True)
    folder_id = fields.Many2one('document.folder', string='Guardar en Carpeta', required=True, tracking=True)

    document_type = fields.Selection([
        ('PR', 'Procedimiento'), ('PL', 'Política'),
        ('MM', 'Marketing'), ('CT', 'Contrato'),
        ('MN', 'Manual'), ('OT', 'Otro')
    ], string='Tipo Doc.', required=True)

    area = fields.Selection([
        ('MKT', 'Marketing'), ('COM', 'Comercial'),
        ('OPR', 'Operaciones'), ('HR', 'RRHH'),
        ('LEG', 'Legal'), ('GEN', 'General')
    ], string='Área', required=True)

    sequence_number = fields.Integer(string='Secuencial', readonly=True)

    # --- 2. Versionado ---
    version = fields.Char(string='Versión', default='1.0', required=True, tracking=True)
    source_document_id = fields.Many2one('document.control', string='Versión Anterior', readonly=True)
    active_revision_id = fields.Many2one('document.control', string='Revisión en Curso', readonly=True)
    
    revision_type = fields.Selection([
        ('major', 'Mayor (v2.0)'),
        ('minor', 'Menor (v1.1)')
    ], string='Tipo de Revisión')

    state = fields.Selection([
        ('draft', 'Datos Iniciales'),
        ('upload', 'Carga de Archivos'),
        ('review', 'En Revisión'),
        ('validate', 'En Aprobación'),
        ('approved', 'Aprobado'),
        ('rejected', 'Rechazado'),
        ('obsolete', 'Obsoleto')
    ], string='Estado', default='draft', tracking=True)

    # --- 3. Archivos ---
    editable_file = fields.Binary(string='Archivo Editable (Word)', attachment=True)
    editable_filename = fields.Char(string='Nombre Archivo Editable')
    pdf_file = fields.Binary(string='PDF Oficial', attachment=True)
    pdf_filename = fields.Char(string='Nombre Archivo PDF')

    # --- 4. Responsables ---
    reviewed_by_id = fields.Many2one('res.users', string='Revisado por (Firma)', readonly=True)
    review_date = fields.Datetime(string='Fecha/Hora Revisión', readonly=True)
    
    approved_by_id = fields.Many2one('res.users', string='Aprobado por (Firma)', readonly=True)
    approval_date = fields.Datetime(string='Fecha/Hora Aprobación', readonly=True)
    owner_id = fields.Many2one('res.users', string='Propietario', default=lambda self: self.env.user, required=True)
    reviewer_ids = fields.Many2many('res.users', 'doc_rev_rel', string='Equipo Revisor')
    approver_ids = fields.Many2many('res.users', 'doc_app_rel', string='Equipo Aprobador')
    
    issue_date = fields.Date(string='Fecha Emisión')
    next_review_date = fields.Date(string='Próxima Revisión')
    is_owner = fields.Boolean(compute='_compute_is_owner')

    _sql_constraints = [
        ('code_version_uniq', 'unique(code, version)', '¡Ya existe una revisión con este código y versión!')
    ]

    @api.depends('owner_id')
    def _compute_is_owner(self):
        for record in self:
            record.is_owner = record.env.user == record.owner_id

    # --- 5. LÓGICA DE NEGOCIO ---

    def action_start_flow(self):
        self.ensure_one()
        if self.code == 'Nuevo':
            domain = [
                ('area', '=', self.area),
                ('document_type', '=', self.document_type),
                ('code', '!=', 'Nuevo'), 
                ('id', '!=', self.id)
            ]
            last = self.search(domain, order='sequence_number desc', limit=1)
            nxt = (last.sequence_number + 1) if last else 1
            self.code = f"{self.area}-{self.document_type}-{nxt:03d}"
            self.sequence_number = nxt
        self.state = 'upload'

    def _create_revision(self, rev_type):
        self.ensure_one()
        try:
            current_v = float(self.version)
        except ValueError:
            current_v = 1.0

        if rev_type == 'major':
            new_v = f"{int(current_v) + 1}.0"
        else:
            new_v = f"{current_v + 0.1:.1f}"

        new_doc = self.copy({
            'version': new_v,
            'state': 'upload',
            'source_document_id': self.id,
            'code': self.code,
            'sequence_number': self.sequence_number,
            'revision_type': rev_type,
            'editable_file': False,
            'pdf_file': False,
            'issue_date': False,
        })
        
        self.active_revision_id = new_doc.id
        
        return {
            'type': 'ir.actions.act_window',
            'name': f'Nueva Versión {new_v}',
            'res_model': 'document.control',
            'view_mode': 'form',
            'res_id': new_doc.id,
            'target': 'current',
        }

    def action_create_minor_rev(self):
        return self._create_revision('minor')

    def action_create_major_rev(self):
        return self._create_revision('major')

    # --- FLUJOS DE APROBACIÓN CON NOTIFICACIONES ---

    def action_submit_review(self):
        self.ensure_one()
        if not self.editable_file or not self.pdf_file:
            raise ValidationError("Faltan archivos nuevos.")
            
        # LÓGICA DE FLUJO CORTO VS LARGO
        if self.revision_type == 'minor':
            # Flujo Corto: Salta directo a Aprobación (Validate)
            if not self.approver_ids:
                 raise ValidationError("Asigna Aprobadores para esta revisión menor.")
            
            self.state = 'validate'
            self.message_post(body="⏩ Revisión Menor: Saltando etapa de revisión técnica.")
            
            # --- NOTIFICACIÓN APROBADORES (Flujo Corto) ---
            for user in self.approver_ids:
                self.activity_schedule(
                    'mail.mail_activity_data_todo', 
                    user_id=user.id, 
                    note=f'Aprobación Requerida (v{self.version}): {self.code}'
                )

        else:
            # Flujo Normal: Pasa a Review
            if not self.reviewer_ids:
                raise ValidationError("Debes asignar Revisores.")
            
            self.state = 'review'
            
            # --- NOTIFICACIÓN REVISORES (Flujo Normal) ---
            for user in self.reviewer_ids:
                self.activity_schedule(
                    'mail.mail_activity_data_todo', 
                    user_id=user.id, 
                    note=f'Revisión Técnica Requerida (v{self.version}): {self.code}'
                )

    def action_review_pass(self):
        self.ensure_one()
        if self.env.user not in self.reviewer_ids and not self.env.user.has_group('base.group_system'):
            raise ValidationError("No estás asignado como Revisor.")
        if not self.approver_ids:
             raise ValidationError("No hay equipo Aprobador asignado.")
             
        self.write({
            'state': 'validate',
            'reviewed_by_id': self.env.user.id,
            'review_date': fields.Datetime.now()
        })
        self.message_post(body="✅ Revisión Técnica Aprobada. Pasando a Aprobación Final.")
        
        # --- NOTIFICACIÓN APROBADORES (Tras revisión técnica) ---
        for user in self.approver_ids:
            self.activity_schedule(
                'mail.mail_activity_data_todo', 
                user_id=user.id, 
                note=f'Visto Bueno del Revisor recibido. Requiere Aprobación Final: {self.code}'
            )

    def action_approve(self):
        self.ensure_one()
        if self.env.user not in self.approver_ids and not self.env.user.has_group('base.group_system'):
            raise ValidationError("No tienes permiso de Aprobación Final.")
        
        self.write({
            'state': 'approved',
            'issue_date': fields.Date.today(),
            'approved_by_id': self.env.user.id,
            'approval_date': fields.Datetime.now()
        })
        self.message_post(body=f"🏆 Versión {self.version} Aprobada Oficialmente.")

        # Archivar versión anterior
        if self.source_document_id:
            self.source_document_id.write({
                'state': 'obsolete',
                'active_revision_id': False 
            })
            self.source_document_id.message_post(body=f"⛔ Documento obsoleto. Reemplazado por versión {self.version}")

    def action_reject(self):
        self.ensure_one()
        self.state = 'upload'
        self.message_post(body="❌ Documento Rechazado/Devuelto para correcciones.")
