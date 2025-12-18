# -*- coding: utf-8 -*-
from odoo import models, fields, api

class DocumentRejectWizard(models.TransientModel):
    _name = 'document.reject.wizard'
    _description = 'Asistente de Motivo de Rechazo'

    # Campo para escribir el motivo
    reject_reason = fields.Text(string='Motivo del Rechazo', required=True, help="Explica por qué se rechaza el documento.")
    
    # Relación con el documento (para saber qué estamos rechazando)
    document_id = fields.Many2one('document.control', string='Documento')

    def action_confirm_reject(self):
        """ Se ejecuta al darle al botón 'Rechazar' del popup """
        self.ensure_one()
        doc = self.document_id
        
        # 1. Mensaje bonito en el historial (Chatter)
        rejection_msg = f"❌ <b>DOCUMENTO RECHAZADO</b><br/><b>Motivo:</b> {self.reject_reason}"
        doc.message_post(body=rejection_msg, message_type='comment', subtype_xmlid='mail.mt_comment')
        
        # 2. Cerrar la tarea pendiente del Aprobador/Revisor actual
        # (Usamos la función que ya creamos en el modelo principal)
        doc._close_activity_for_current_user(f"Rechazado: {self.reject_reason}")

        # 3. Cambiar el estado hacia atrás (A 'Carga' para que corrijan)
        doc.state = 'upload'
        
        # 4. Crear una actividad para el Dueño avisándole que le rechazaron el doc
        doc.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=doc.owner_id.id,
            summary='🔴 Documento Rechazado',
            note=f'Se ha rechazado tu documento por: {self.reject_reason}. Por favor corrige y vuelve a enviar.',
        )

        return {'type': 'ir.actions.act_window_close'}
